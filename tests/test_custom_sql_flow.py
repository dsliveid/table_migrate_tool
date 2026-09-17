import sys
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from src.core.config import AppConfig
from src.gui.widgets.object_selector_table import ObjectSelectorTable
from src.db.generator import DDLGenerator
from src.gui.main_window import MainWindow

app = QApplication.instance()
if app is None:
    app = QApplication([])


class TestCustomSQLFlow(unittest.TestCase):

    def setUp(self):
        AppConfig.initialize()

    def test_single_view_custom_sql_preservation(self):
        """测试单视图自定义 SQL 调整后，get_selected_objects 正确包含 custom_sql"""
        selector = ObjectSelectorTable()
        mock_objects = [
            {"schema": "dbo", "name": "Users", "type": "TABLE", "row_count": 10},
            {"schema": "dbo", "name": "v_users", "type": "VIEW", "row_count": 0}
        ]
        selector.load_objects(mock_objects)

        # 初始状态：custom_sql 应为 None
        selected = selector.get_selected_objects()
        self.assertEqual(len(selected), 2)
        v_obj = [o for o in selected if o["name"] == "v_users"][0]
        self.assertIsNone(v_obj.get("custom_sql"))

        # 模拟编辑自定义 SQL 并保存
        custom_query = "CREATE VIEW [dbo].[v_users] AS SELECT id, username FROM [192.168.200.45].[medical].[dbo].[users];"
        selector.objects_data[1]["custom_sql"] = custom_query
        selector._update_view_sql_button(1, selector.objects_data[1])
        selector._update_stats()

        # 验证按钮显示为已自定义
        btn = selector.table.cellWidget(1, 5)
        self.assertIn("已自定义", btn.text())

        # 验证 get_selected_objects 正确返回了 custom_sql
        selected_after = selector.get_selected_objects()
        v_obj_after = [o for o in selected_after if o["name"] == "v_users"][0]
        self.assertEqual(v_obj_after.get("custom_sql"), custom_query)

        # 验证 stats 提示
        self.assertIn("含自定义SQL: 1 个", selector.lbl_stats.text())

    @patch("PySide6.QtWidgets.QMessageBox.information")
    def test_batch_replace_view_sql_preservation(self, mock_info):
        """测试批量替换视图 SQL 文本后，所有匹配视图的 custom_sql 均生效"""
        selector = ObjectSelectorTable()
        mock_objects = [
            {"schema": "dbo", "name": "v_dept", "type": "VIEW", "row_count": 0},
            {"schema": "dbo", "name": "v_user", "type": "VIEW", "row_count": 0},
        ]
        raw_ddl = {
            "v_dept": "CREATE VIEW [dbo].[v_dept] AS SELECT * FROM [192.168.230.6].[old_db].[dbo].[dept];",
            "v_user": "CREATE VIEW [dbo].[v_user] AS SELECT * FROM [192.168.230.6].[old_db].[dbo].[user];"
        }
        selector.set_source_view_fetcher(lambda s, n: raw_ddl.get(n, ""))
        selector.load_objects(mock_objects)

        # 模拟批量替换对话框行为
        from src.gui.view_sql_dialog import BatchReplaceViewSqlDialog
        views_to_process = [
            {"schema": "dbo", "name": "v_dept", "custom_sql": None, "is_checked": True, "_row": 0},
            {"schema": "dbo", "name": "v_user", "custom_sql": None, "is_checked": True, "_row": 1},
        ]
        dlg = BatchReplaceViewSqlDialog(views_config=views_to_process, fetch_raw_callback=selector.source_view_fetcher)
        dlg.edit_find.setText("192.168.230.6")
        dlg.edit_replace.setText("192.168.200.45")
        dlg._on_apply()

        self.assertEqual(dlg.replaced_count, 2)
        self.assertIn("192.168.200.45", views_to_process[0]["custom_sql"])
        self.assertIn("192.168.200.45", views_to_process[1]["custom_sql"])

        # 同步应用到 selector
        for vw in views_to_process:
            r = vw["_row"]
            selector.objects_data[r]["custom_sql"] = vw.get("custom_sql")
            selector._update_view_sql_button(r, selector.objects_data[r])
        selector._update_stats()

        selected = selector.get_selected_objects()
        for item in selected:
            self.assertIn("192.168.200.45", item["custom_sql"])
            self.assertNotIn("192.168.230.6", item["custom_sql"])

        self.assertIn("含自定义SQL: 2 个", selector.lbl_stats.text())

    def test_restore_selection_custom_sql(self):
        """测试应用保存的方案时，custom_sql 能够完整恢复并被导出"""
        selector = ObjectSelectorTable()
        mock_objects = [
            {"schema": "dbo", "name": "tbl1", "type": "TABLE", "row_count": 50},
            {"schema": "dbo", "name": "v_test", "type": "VIEW", "row_count": 0}
        ]
        selector.load_objects(mock_objects)

        saved_plan_config = [
            {"schema": "dbo", "name": "tbl1", "type": "TABLE", "migrate_data": True},
            {
                "schema": "dbo",
                "name": "v_test",
                "type": "VIEW",
                "migrate_data": False,
                "custom_sql": "CREATE VIEW [dbo].[v_test] AS SELECT 999 AS my_col;"
            }
        ]
        selector.restore_selection(saved_plan_config)

        # 检查内部数据及导出
        selected = selector.get_selected_objects()
        self.assertEqual(len(selected), 2)
        v = [o for o in selected if o["name"] == "v_test"][0]
        self.assertEqual(v["custom_sql"], "CREATE VIEW [dbo].[v_test] AS SELECT 999 AS my_col;")

        # 按钮外观应更新为已自定义
        btn = selector.table.cellWidget(1, 5)
        self.assertIn("已自定义", btn.text())

    def test_reload_objects_with_preserve_config(self):
        """测试平滑重新扫描源库时，原有的勾选状态、模式及自定义 SQL 不会丢失"""
        selector = ObjectSelectorTable()
        mock_objects_v1 = [
            {"schema": "dbo", "name": "tbl1", "type": "TABLE", "row_count": 50},
            {"schema": "dbo", "name": "v_test", "type": "VIEW", "row_count": 0}
        ]
        selector.load_objects(mock_objects_v1)
        selector.objects_data[1]["custom_sql"] = "CREATE VIEW [dbo].[v_test] AS SELECT 123;"

        # 保存当前所有配置
        old_configs = selector.get_all_objects_config()

        # 模拟源库新增了一张新表 tbl2
        mock_objects_v2 = [
            {"schema": "dbo", "name": "tbl1", "type": "TABLE", "row_count": 50},
            {"schema": "dbo", "name": "v_test", "type": "VIEW", "row_count": 0},
            {"schema": "dbo", "name": "tbl2", "type": "TABLE", "row_count": 10},
        ]
        selector.load_objects(mock_objects_v2, preserve_config=old_configs)

        # 验证 v_test 的自定义 SQL 依然保留
        selected = selector.get_selected_objects()
        self.assertEqual(len(selected), 3)
        v = [o for o in selected if o["name"] == "v_test"][0]
        self.assertEqual(v["custom_sql"], "CREATE VIEW [dbo].[v_test] AS SELECT 123;")

    def test_ddl_generator_split_go_batches(self):
        """测试 DDLGenerator 对包含 GO 语句的切分"""
        sql_with_go = """
        SET ANSI_NULLS ON
        GO
        SET QUOTED_IDENTIFIER ON
        GO
        CREATE VIEW [dbo].[v_test] AS SELECT 1 AS a;
        GO
        """
        batches = DDLGenerator._split_sql_batches(sql_with_go)
        self.assertEqual(len(batches), 3)
        self.assertEqual(batches[0], "SET ANSI_NULLS ON")
        self.assertEqual(batches[1], "SET QUOTED_IDENTIFIER ON")
        self.assertIn("CREATE VIEW", batches[2])

        stmts = DDLGenerator.generate_create_view_from_sql("dbo", "v_test", sql_with_go)
        # 第一条是 DROP VIEW，后续是切分出的 batches
        self.assertTrue(stmts[0].startswith("IF OBJECT_ID"))
        self.assertEqual(len(stmts), 4)

    def test_main_window_step2_cache_navigation(self):
        """测试在主窗口中，数据源和数据库未改变时进入步骤2不会重复清空已有配置"""
        with patch.object(MainWindow, "_load_src_databases"), \
             patch.object(MainWindow, "_load_tgt_databases"), \
             patch.object(MainWindow, "_refresh_datasources"):
            win = MainWindow()
            win.all_datasources = [
                {"id": 1, "name": "DS1", "host": "127.0.0.1", "port": 1433},
                {"id": 2, "name": "DS2", "host": "127.0.0.1", "port": 1433}
            ]
            win.combo_src_ds.addItem("DS1", 1)
            win.combo_tgt_ds.addItem("DS2", 2)
            win.combo_src_ds.setCurrentIndex(0)
            win.combo_tgt_ds.setCurrentIndex(0)
            win.combo_src_db.setEditText("test_db")
            win.combo_tgt_db.setEditText("target_db")

            # 预设 selector 数据
            win.object_selector.load_objects([
                {"schema": "dbo", "name": "Users", "type": "TABLE", "row_count": 10},
                {"schema": "dbo", "name": "v_test", "type": "VIEW", "row_count": 0}
            ])
            win.object_selector.objects_data[1]["custom_sql"] = "MY_CUSTOM_SQL"
            win._loaded_ds_key = (1, "test_db")

            # 模拟从第1步点击进入第2步
            with patch.object(win, "_load_source_objects") as mock_load:
                win._goto_step2()
                mock_load.assert_not_called()  # 不应该重新从数据库拉取
                self.assertEqual(win.stacked_widget.currentIndex(), 1)
                # 确认自定义 SQL 依然保留
                selected = win.object_selector.get_selected_objects()
                v = [o for o in selected if o["name"] == "v_test"][0]
                self.assertEqual(v["custom_sql"], "MY_CUSTOM_SQL")


if __name__ == "__main__":
    unittest.main()
