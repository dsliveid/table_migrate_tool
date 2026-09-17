import os
import sys
import tempfile
import zipfile
import unittest
from datetime import datetime, date
from decimal import Decimal
from uuid import UUID
from unittest.mock import MagicMock, patch
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from src.core.config import AppConfig
from src.core.storage import AppStorage
from src.db.metadata import TableMetadata, ColumnMetadata, IndexMetadata, ViewMetadata
from src.db.exporter import ExportEngine, ExportTaskConfig, ExportProgressSignal, format_sql_literal
from src.gui.toolbox_widget import ToolboxWidget
from src.gui.export_widget import ExportWidget
from src.gui.main_window import MainWindow
from src.gui.plan_dialog import PlanDialog, SavePlanDialog

# Create QApplication instance once for GUI tests
app = QApplication.instance()
if app is None:
    app = QApplication([])


class TestExportFeature(unittest.TestCase):

    def setUp(self):
        AppConfig.initialize()
        self.storage = AppStorage()

    def test_storage_export_plans(self):
        """测试导出方案的增删改查 (CRUD)"""
        plan_data = {
            "name": "测试导出方案_核心表",
            "description": "每日结构及配置导出",
            "source_ds_id": 1,
            "source_db": "SourceDB_Test",
            "export_mode": "zip",
            "objects_config": [
                {"schema": "dbo", "name": "Users", "type": "TABLE", "export_data": True},
                {"schema": "dbo", "name": "v_ActiveUsers", "type": "VIEW", "export_data": False, "custom_sql": "SELECT 1"}
            ]
        }
        plan_id = self.storage.save_export_plan(plan_data)
        self.assertIsNotNone(plan_id)

        # 检查获取单条
        fetched = self.storage.get_export_plan(plan_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["name"], "测试导出方案_核心表")
        self.assertEqual(fetched["source_db"], "SourceDB_Test")
        self.assertEqual(fetched["export_mode"], "zip")
        self.assertEqual(len(fetched["objects_config"]), 2)
        self.assertTrue(fetched["objects_config"][0]["export_data"])

        # 检查重命名
        new_name = "测试导出方案_核心表_重命名"
        self.assertTrue(self.storage.rename_export_plan(plan_id, new_name))
        renamed = self.storage.get_export_plan(plan_id)
        self.assertEqual(renamed["name"], new_name)

        # 检查获取列表
        plans = self.storage.get_export_plans()
        self.assertTrue(any(p["id"] == plan_id for p in plans))

        # 检查删除
        self.assertTrue(self.storage.delete_export_plan(plan_id))
        self.assertIsNone(self.storage.get_export_plan(plan_id))

    def test_format_sql_literal(self):
        """测试 Python 数据类型向 T-SQL 字面量的安全转换"""
        self.assertEqual(format_sql_literal(None), "NULL")
        self.assertEqual(format_sql_literal(True), "1")
        self.assertEqual(format_sql_literal(False), "0")
        self.assertEqual(format_sql_literal(123), "123")
        self.assertEqual(format_sql_literal(45.67), "45.67")
        self.assertEqual(format_sql_literal(Decimal("99.99")), "99.99")
        # 字符串单引号转义
        self.assertEqual(format_sql_literal("O'Reilly"), "N'O''Reilly'")
        self.assertEqual(format_sql_literal("普通文本"), "N'普通文本'")
        # 二进制 0x
        self.assertEqual(format_sql_literal(b"\x01\x02\xab"), "0x0102ab")
        # 日期与时间
        dt = datetime(2026, 9, 17, 14, 30, 0, 123000)
        self.assertEqual(format_sql_literal(dt), "'2026-09-17 14:30:00.123'")
        d = date(2026, 9, 17)
        self.assertEqual(format_sql_literal(d), "'2026-09-17'")
        # UUID
        u = UUID("12345678-1234-5678-1234-567812345678")
        self.assertEqual(format_sql_literal(u), "'12345678-1234-5678-1234-567812345678'")

    @patch("src.db.exporter.MSSQLConnection")
    @patch("src.db.exporter.DatabaseMetadataExtractor")
    def test_export_engine_single_sql(self, mock_extractor_cls, mock_conn_cls):
        """测试单个 SQL 脚本文件的导出：表结构、包含数据（多批次+IDENTITY）、视图定义"""
        with tempfile.TemporaryDirectory() as tmpdir:
            sql_file = os.path.join(tmpdir, "test_output.sql")

            # 模拟源表元数据
            mock_table = TableMetadata(
                schema="dbo",
                name="Users",
                columns=[
                    ColumnMetadata(name="id", data_type="int", max_length=4, precision=10, scale=0, is_nullable=False, is_identity=True),
                    ColumnMetadata(name="username", data_type="nvarchar", max_length=100, precision=0, scale=0, is_nullable=False),
                    ColumnMetadata(name="is_active", data_type="bit", max_length=1, precision=1, scale=0, is_nullable=False),
                ],
                primary_key=IndexMetadata(name="PK_Users", is_unique=True, is_primary_key=True, type_desc="CLUSTERED", columns=[("id", False)])
            )

            # 模拟视图元数据
            mock_view = ViewMetadata(
                schema="dbo",
                name="v_users",
                definition="CREATE VIEW [dbo].[v_users] AS SELECT id, username FROM Users;"
            )

            mock_extractor = MagicMock()
            mock_extractor.extract_table.return_value = mock_table
            mock_extractor.extract_view.return_value = mock_view
            mock_extractor_cls.return_value = mock_extractor

            # 模拟连接与数据查询
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            # 模拟返回 2 行数据，然后返回空
            mock_cursor.fetchmany.side_effect = [
                [
                    {"id": 1, "username": "Alice", "is_active": True},
                    {"id": 2, "username": "Bob's Team", "is_active": False},
                ],
                []
            ]
            mock_conn._conn.cursor.return_value = mock_cursor
            mock_conn_cls.return_value = mock_conn

            config = ExportTaskConfig(
                source_ds={"name": "TestDS", "host": "127.0.0.1", "port": 1433},
                source_db="TestDB",
                objects=[
                    {"schema": "dbo", "name": "Users", "type": "TABLE", "export_data": True},
                    {"schema": "dbo", "name": "v_users", "type": "VIEW", "custom_sql": "CREATE VIEW [dbo].[v_users] AS SELECT 123;"}
                ],
                output_path=sql_file,
                export_mode="single_sql"
            )

            engine = ExportEngine(config)
            summary = engine.execute()

            self.assertTrue(summary["success"])
            self.assertEqual(summary["tables_processed"], 1)
            self.assertEqual(summary["views_processed"], 1)
            self.assertEqual(summary["total_rows_exported"], 2)
            self.assertTrue(os.path.exists(sql_file))

            # 读取生成内容验证
            with open(sql_file, "r", encoding="utf-8-sig") as f:
                content = f.read()

            self.assertIn("CREATE TABLE [dbo].[Users]", content)
            self.assertIn("SET IDENTITY_INSERT [dbo].[Users] ON;", content)
            self.assertIn("N'Alice'", content)
            self.assertIn("N'Bob''s Team'", content)
            self.assertIn("SET IDENTITY_INSERT [dbo].[Users] OFF;", content)
            self.assertIn("SELECT 123;", content)  # 自定义视图 SQL 生效

    @patch("src.db.exporter.MSSQLConnection")
    @patch("src.db.exporter.DatabaseMetadataExtractor")
    def test_export_engine_zip_archive(self, mock_extractor_cls, mock_conn_cls):
        """测试多表分文件并合并为 ZIP 压缩包的导出"""
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_file = os.path.join(tmpdir, "test_output.zip")

            t1 = TableMetadata(schema="dbo", name="T1", columns=[ColumnMetadata(name="id", data_type="int", max_length=4, precision=10, scale=0, is_nullable=False)])
            t2 = TableMetadata(schema="dbo", name="T2", columns=[ColumnMetadata(name="id", data_type="int", max_length=4, precision=10, scale=0, is_nullable=False)])
            v1 = ViewMetadata(schema="dbo", name="V1", definition="CREATE VIEW [dbo].[V1] AS SELECT 1;")

            mock_extractor = MagicMock()
            mock_extractor.extract_table.side_effect = lambda s, n: t1 if n == "T1" else t2
            mock_extractor.extract_view.return_value = v1
            mock_extractor_cls.return_value = mock_extractor

            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchmany.return_value = []
            mock_conn._conn.cursor.return_value = mock_cursor
            mock_conn_cls.return_value = mock_conn

            config = ExportTaskConfig(
                source_ds={"name": "TestDS", "host": "127.0.0.1"},
                source_db="TestDB",
                objects=[
                    {"schema": "dbo", "name": "T1", "type": "TABLE", "export_data": True},
                    {"schema": "dbo", "name": "T2", "type": "TABLE", "export_data": False},
                    {"schema": "dbo", "name": "V1", "type": "VIEW"}
                ],
                output_path=zip_file,
                export_mode="zip"
            )

            engine = ExportEngine(config)
            summary = engine.execute()

            self.assertTrue(summary["success"])
            self.assertTrue(os.path.exists(zip_file))

            # 验证 ZIP 内部文件结构
            with zipfile.ZipFile(zip_file, "r") as zf:
                file_list = zf.namelist()
                self.assertIn("dbo.T1.sql", file_list)
                self.assertIn("dbo.T2.sql", file_list)
                self.assertIn("views.sql", file_list)
                self.assertIn("_README.txt", file_list)

    def test_toolbox_widget_signals(self):
        """测试工具箱首页卡片与信号发射"""
        toolbox = ToolboxWidget()
        self.assertIsNotNone(toolbox)

        # 验证信号绑定
        mig_fired = []
        exp_fired = []
        toolbox.request_open_migration.connect(lambda: mig_fired.append(True))
        toolbox.request_open_export.connect(lambda: exp_fired.append(True))

        toolbox.card_migration.clicked.emit()
        self.assertEqual(len(mig_fired), 1)

        toolbox.card_export.clicked.emit()
        self.assertEqual(len(exp_fired), 1)

    def test_export_mode_inference_in_step3(self):
        """测试步骤3智能打包策略推断：纯结构选单SQL，多表包含数据自动选ZIP"""
        export_widget = ExportWidget(storage=self.storage)

        # 1. 模拟选中多个表，且多表均包含数据
        objects_with_multi_data = [
            {"schema": "dbo", "name": "Users", "type": "TABLE", "export_data": True, "is_checked": True},
            {"schema": "dbo", "name": "Orders", "type": "TABLE", "export_data": True, "is_checked": True},
            {"schema": "dbo", "name": "v_test", "type": "VIEW", "export_data": False, "is_checked": True}
        ]
        export_widget.object_selector.get_selected_objects = MagicMock(return_value=objects_with_multi_data)
        export_widget.combo_src_db.setEditText("MyCorpDB")
        export_widget._goto_step3()

        # 应自动切换为 ZIP 模式
        self.assertTrue(export_widget.rb_mode_zip.isChecked())
        self.assertTrue(export_widget.edit_output_path.toPlainText().endswith(".zip"))

        # 2. 模拟全部为纯结构（无数据）
        objects_schema_only = [
            {"schema": "dbo", "name": "Users", "type": "TABLE", "export_data": False, "is_checked": True},
            {"schema": "dbo", "name": "Orders", "type": "TABLE", "export_data": False, "is_checked": True},
            {"schema": "dbo", "name": "v_test", "type": "VIEW", "export_data": False, "is_checked": True}
        ]
        export_widget.object_selector.get_selected_objects = MagicMock(return_value=objects_schema_only)
        export_widget._goto_step3()

        # 应自动切换为单 SQL 模式
        self.assertTrue(export_widget.rb_mode_single.isChecked())
        self.assertTrue(export_widget.edit_output_path.toPlainText().endswith(".sql"))

    def test_main_window_root_stack_navigation(self):
        """测试主窗口工具箱与迁移、导出页面之间的平滑切换"""
        win = MainWindow()
        # 初始在工具箱
        self.assertEqual(win.root_stack.currentIndex(), 0)

        # 点击进入迁移
        win.toolbox_widget.request_open_migration.emit()
        self.assertEqual(win.root_stack.currentIndex(), 1)

        # 点击返回工具箱
        win.btn_back_to_toolbox.click()
        self.assertEqual(win.root_stack.currentIndex(), 0)

        # 点击进入导出
        with patch.object(win.export_widget, "_load_src_databases"):
            win.toolbox_widget.request_open_export.emit()
            self.assertEqual(win.root_stack.currentIndex(), 2)

        # 导出向导点击返回工具箱
        win.export_widget.btn_back_to_box.click()
        self.assertEqual(win.root_stack.currentIndex(), 0)

    def test_plan_dialog_export_type(self):
        """测试 PlanDialog 和 SavePlanDialog 在 export 模式下的正常初始化与列适配"""
        plan_dlg = PlanDialog(plan_type="export")
        self.assertEqual(plan_dlg.windowTitle(), "导出方案管理")
        self.assertEqual(plan_dlg.table.columnCount(), 5)
        self.assertIn("导出模式", [plan_dlg.table.horizontalHeaderItem(i).text() for i in range(5)])

        save_dlg = SavePlanDialog(storage=self.storage, plan_type="export")
        self.assertEqual(save_dlg.windowTitle(), "保存导出方案 / 规则")
        self.assertEqual(save_dlg.plan_type, "export")

    def test_export_widget_load_databases_and_objects(self):
        """测试 ExportWidget 从数据源加载数据库列表及对象列表（验证无属性缺失）"""
        export_widget = ExportWidget(storage=self.storage)

        # 模拟数据源
        fake_ds = {"id": 1, "name": "TestDS", "host": "127.0.0.1", "port": 1433, "user": "sa", "password": "pwd"}
        export_widget.all_datasources = [fake_ds]
        export_widget.combo_src_ds.clear()
        export_widget.combo_src_ds.addItem("TestDS", 1)

        # 1. 模拟 conn.get_databases
        with patch("src.gui.export_widget.MSSQLConnection") as mock_conn_cls:
            mock_conn = MagicMock()
            mock_conn.get_databases.return_value = ["master", "test_db"]
            mock_conn_cls.return_value.__enter__.return_value = mock_conn

            export_widget._load_src_databases()
            self.assertEqual(export_widget.combo_src_db.get_databases(), ["master", "test_db"])

        # 2. 模拟 extractor.list_objects
        with patch("src.gui.export_widget.MSSQLConnection") as mock_conn_cls, \
             patch("src.gui.export_widget.DatabaseMetadataExtractor") as mock_extractor_cls:
            mock_conn = MagicMock()
            mock_conn_cls.return_value.__enter__.return_value = mock_conn

            mock_extractor = MagicMock()
            mock_extractor.list_objects.return_value = [
                {"schema": "dbo", "name": "Users", "type": "TABLE", "row_count": 10}
            ]
            mock_extractor_cls.return_value = mock_extractor

            export_widget._load_source_objects()
            self.assertEqual(len(export_widget.loaded_objects), 1)
            self.assertEqual(export_widget.loaded_objects[0]["name"], "Users")

    def test_drop_and_skip_checkboxes_mutually_exclusive(self):
        """测试 DROP IF EXISTS 与 IF NOT EXISTS 复选框的互斥逻辑"""
        export_widget = ExportWidget(storage=self.storage)

        # 默认：chk_drop 为 True, chk_skip 为 False
        self.assertTrue(export_widget.chk_drop.isChecked())
        self.assertFalse(export_widget.chk_skip.isChecked())

        # 勾选 chk_skip -> chk_drop 应自动变为 False
        export_widget.chk_skip.setChecked(True)
        self.assertTrue(export_widget.chk_skip.isChecked())
        self.assertFalse(export_widget.chk_drop.isChecked())

        # 再次勾选 chk_drop -> chk_skip 应自动变为 False
        export_widget.chk_drop.setChecked(True)
        self.assertTrue(export_widget.chk_drop.isChecked())
        self.assertFalse(export_widget.chk_skip.isChecked())

        # 允许两者均不勾选（标准直接创建）
        export_widget.chk_drop.setChecked(False)
        self.assertFalse(export_widget.chk_drop.isChecked())
        self.assertFalse(export_widget.chk_skip.isChecked())

    @patch("src.db.exporter.MSSQLConnection")
    @patch("src.db.exporter.DatabaseMetadataExtractor")
    def test_export_engine_skip_if_exists(self, mock_extractor_cls, mock_conn_cls):
        """测试如果存在则跳过创建 (include_skip=True) 生成的 T-SQL"""
        with tempfile.TemporaryDirectory() as tmpdir:
            sql_file = os.path.join(tmpdir, "skip_test.sql")

            mock_table = TableMetadata(
                schema="dbo",
                name="Products",
                columns=[
                    ColumnMetadata(name="id", data_type="int", max_length=4, precision=10, scale=0, is_nullable=False, is_identity=True),
                    ColumnMetadata(name="title", data_type="nvarchar", max_length=100, precision=0, scale=0, is_nullable=True, description="产品名")
                ],
                primary_key=IndexMetadata(name="PK_Products", is_unique=True, is_primary_key=True, type_desc="CLUSTERED", columns=[("id", False)]),
                indexes=[
                    IndexMetadata(name="IX_title", is_unique=False, is_primary_key=False, type_desc="NONCLUSTERED", columns=[("title", False)])
                ],
                description="商品表"
            )

            mock_view = ViewMetadata(
                schema="dbo",
                name="v_products",
                definition="CREATE VIEW [dbo].[v_products] AS SELECT id, title FROM [dbo].[Products];",
                description="商品视图"
            )

            mock_extractor = MagicMock()
            mock_extractor.extract_table.return_value = mock_table
            mock_extractor.extract_view.return_value = mock_view
            mock_extractor_cls.return_value = mock_extractor

            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchmany.return_value = []
            mock_conn._conn.cursor.return_value = mock_cursor
            mock_conn_cls.return_value.__enter__.return_value = mock_conn

            config = ExportTaskConfig(
                source_ds={"id": 1, "name": "TestDS"},
                source_db="ShopDB",
                objects=[
                    {"schema": "dbo", "name": "Products", "type": "TABLE", "export_data": False},
                    {"schema": "dbo", "name": "v_products", "type": "VIEW", "export_data": False}
                ],
                output_path=sql_file,
                export_mode="single_sql",
                include_drop=False,
                include_skip=True,
                include_comments=True,
                include_indexes=True
            )

            engine = ExportEngine(config)
            summary = engine.execute()
            self.assertTrue(summary["success"])

            with open(sql_file, "r", encoding="utf-8-sig") as f:
                content = f.read()

            # 1. 验证不含 DROP 语句
            self.assertNotIn("DROP TABLE", content)
            self.assertNotIn("DROP VIEW", content)

            # 2. 验证表存在性检查 (IF OBJECT_ID ... IS NULL BEGIN ... END)
            self.assertIn("IF OBJECT_ID(N'[dbo].[Products]', 'U') IS NULL", content)
            self.assertIn("CREATE TABLE [dbo].[Products]", content)

            # 3. 验证索引与注释存在性检查
            self.assertIn("IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[Products]') AND name = N'IX_title')", content)
            self.assertIn("IF NOT EXISTS (SELECT 1 FROM sys.fn_listextendedproperty(N'MS_Description', N'SCHEMA', N'dbo', N'TABLE', N'Products', NULL, NULL))", content)

            # 4. 验证视图跳过检查 (IF OBJECT_ID ... IS NULL BEGIN EXEC sp_executesql ... END)
            self.assertIn("IF OBJECT_ID(N'[dbo].[v_products]', 'V') IS NULL", content)
            self.assertIn("EXEC sp_executesql", content)


if __name__ == "__main__":
    unittest.main()

