import sys
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QComboBox
from PySide6.QtCore import Qt
from src.core.config import AppConfig
from src.gui.main_window import MainWindow
from src.gui.widgets.object_selector_table import ObjectSelectorTable, NoWheelComboBox


# Create QApplication instance once for test suite
app = QApplication.instance()
if app is None:
    app = QApplication([])


class TestGUIComponents(unittest.TestCase):

    def setUp(self):
        AppConfig.initialize()

    def test_object_selector_table_rules(self):
        """测试对象选择表格：默认全选、视图强制仅结构不可选数据规则"""
        selector = ObjectSelectorTable()
        mock_objects = [
            {"schema": "dbo", "name": "Users", "type": "TABLE", "row_count": 100},
            {"schema": "dbo", "name": "v_ActiveUsers", "type": "VIEW", "row_count": 0},
            {"schema": "dbo", "name": "Orders", "type": "TABLE", "row_count": 500}
        ]
        selector.load_objects(mock_objects)

        # 1. 验证默认全选
        self.assertEqual(selector.table.rowCount(), 3)
        for r in range(3):
            item = selector.table.item(r, 0)
            self.assertEqual(item.checkState(), Qt.Checked)

        # 2. 验证视图的规则：强制不可选迁移数据 (Combo disabled)
        view_combo = selector.table.cellWidget(1, 4)
        self.assertIsInstance(view_combo, QComboBox)
        self.assertFalse(view_combo.isEnabled(), "视图的迁移内容选项必须被禁用/只读")
        self.assertIn("仅迁移结构", view_combo.currentText())

        # 3. 验证表可以切换为包含数据，且禁用滚轮切换 (避免误触)
        table_combo = selector.table.cellWidget(0, 4)
        self.assertIsInstance(table_combo, NoWheelComboBox)
        self.assertTrue(table_combo.isEnabled())
        
        # 验证鼠标滚轮事件被忽略且不会更改选项
        from PySide6.QtGui import QWheelEvent
        from PySide6.QtCore import QPointF, QPoint
        table_combo.setCurrentIndex(0)
        wheel_event = QWheelEvent(
            QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, -120),
            Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False
        )
        table_combo.wheelEvent(wheel_event)
        self.assertEqual(table_combo.currentIndex(), 0, "滚轮事件不应改变下拉框当前选中项")
        self.assertFalse(wheel_event.isAccepted(), "滚轮事件应被忽略以传递给父级组件滚动")

        # 验证手动选择可以正常更改
        table_combo.setCurrentIndex(1)  # 结构 + 数据
        self.assertEqual(table_combo.currentIndex(), 1)

        # 4. 获取已选列表
        selected = selector.get_selected_objects()
        self.assertEqual(len(selected), 3)
        # Users: 表，勾选了数据
        self.assertTrue(selected[0]["migrate_data"])
        # v_ActiveUsers: 视图，必须为 False
        self.assertFalse(selected[1]["migrate_data"])
        # Orders: 表，默认仅结构
        self.assertFalse(selected[2]["migrate_data"])

    def test_main_window_init_and_steps(self):
        """测试主窗口初始化和页面切换逻辑"""
        win = MainWindow()
        self.assertIsNotNone(win)
        self.assertEqual(win.stacked_widget.count(), 5)
        self.assertEqual(win.stacked_widget.currentIndex(), 0)

        # 切换步骤
        win._set_current_step(1)
        self.assertEqual(win.stacked_widget.currentIndex(), 1)
        win._set_current_step(2)
        self.assertEqual(win.stacked_widget.currentIndex(), 2)
        win._set_current_step(3)
        self.assertEqual(win.stacked_widget.currentIndex(), 3)
        win._set_current_step(4)
        self.assertEqual(win.stacked_widget.currentIndex(), 4)

    def test_database_combo_and_main_window_unification(self):
        """测试来源数据库与目标数据库统一控件：可编辑、支持输入联想与下拉"""
        from src.gui.widgets.database_combo import DatabaseComboBox
        
        win = MainWindow()
        # 1. 验证两者均为 DatabaseComboBox 统一类型
        self.assertIsInstance(win.combo_src_db, DatabaseComboBox)
        self.assertIsInstance(win.combo_tgt_db, DatabaseComboBox)

        # 2. 验证两者均开启编辑与禁止意外插入策略
        self.assertTrue(win.combo_src_db.isEditable())
        self.assertTrue(win.combo_tgt_db.isEditable())
        self.assertEqual(win.combo_src_db.insertPolicy(), QComboBox.NoInsert)
        self.assertEqual(win.combo_tgt_db.insertPolicy(), QComboBox.NoInsert)

        # 3. 验证自动补全器配置
        for combo in (win.combo_src_db, win.combo_tgt_db):
            comp = combo.completer()
            self.assertIsNotNone(comp)
            self.assertEqual(comp.filterMode(), Qt.MatchContains)
            self.assertEqual(comp.caseSensitivity(), Qt.CaseInsensitive)

        # 4. 验证 set_databases 保持输入与查找逻辑
        test_dbs = ["master", "model", "msdb", "tempdb", "AHISTER", "AHISTER_BAK", "CRM_DATA"]
        combo = DatabaseComboBox(placeholder="测试占位符")
        combo.set_databases(test_dbs)
        self.assertEqual(combo.count(), len(test_dbs))

        # 手动输入一个存在的库
        combo.setEditText("AHISTER")
        self.assertEqual(combo.currentText(), "AHISTER")
        # 重新刷新数据库列表，应平滑保持 "AHISTER"
        combo.set_databases(test_dbs)
        self.assertEqual(combo.currentText(), "AHISTER")
        self.assertEqual(combo.currentIndex(), test_dbs.index("AHISTER"))

        # 手动输入一个新库名（不存在于已有库中）
        combo.setEditText("MyBrandNewDB")
        self.assertEqual(combo.currentText(), "MyBrandNewDB")
        # 再次刷新，仍能保持输入的新库名，且不会污染原有 dbs 列表
        combo.set_databases(test_dbs)
        self.assertEqual(combo.currentText(), "MyBrandNewDB")
        self.assertEqual(combo.count(), len(test_dbs))

    @patch("src.gui.main_window.QMessageBox.information")
    @patch("src.gui.main_window.SavePlanDialog")
    def test_main_window_save_plan_integration(self, mock_dialog_cls, mock_info):
        """测试主窗口保存方案时与 SavePlanDialog 的集成联动与 current_plan_id 保存"""
        from PySide6.QtWidgets import QDialog
        win = MainWindow()
        win.storage = MagicMock()
        win.storage.save_plan.return_value = 88

        # 1. 模拟从已保存方案中加载
        mock_plan = {
            "id": 88,
            "name": "测试方案88",
            "source_db": "src",
            "target_db": "tgt",
            "strategy": "diff",
            "objects_config": []
        }
        win._load_saved_plan(mock_plan)
        self.assertEqual(win.current_plan_id, 88)

        # 2. 模拟点击保存按钮触发 _on_save_plan
        mock_dialog_instance = MagicMock()
        mock_dialog_instance.exec.return_value = QDialog.Accepted
        mock_dialog_instance.get_save_target.return_value = (88, "测试方案88")
        mock_dialog_cls.return_value = mock_dialog_instance

        win._on_save_plan()

        # 验证传入了当前的 current_plan_id
        mock_dialog_cls.assert_called_with(storage=win.storage, current_plan_id=88, parent=win)
        # 验证调用了 storage.save_plan，并带有 id 88 和 name "测试方案88"
        win.storage.save_plan.assert_called_once()
        saved_arg = win.storage.save_plan.call_args[0][0]
        self.assertEqual(saved_arg["id"], 88)
        self.assertEqual(saved_arg["name"], "测试方案88")
        self.assertEqual(win.current_plan_id, 88)


if __name__ == "__main__":
    unittest.main()

