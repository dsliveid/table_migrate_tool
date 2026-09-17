import sys
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QMessageBox, QDialog, QInputDialog
from src.core.config import AppConfig
from src.core.storage import AppStorage
from src.gui.plan_dialog import SavePlanDialog, PlanDialog


# Create QApplication instance once for test suite
app = QApplication.instance()
if app is None:
    app = QApplication([])


class TestSavePlanDialog(unittest.TestCase):

    def setUp(self):
        AppConfig.initialize()
        self.mock_storage = MagicMock()

    def test_empty_plans_state(self):
        """测试无历史方案/规则时的初始状态：覆盖模式禁用，默认选中新建"""
        self.mock_storage.get_plans.return_value = []
        dlg = SavePlanDialog(storage=self.mock_storage, current_plan_id=None)

        self.assertFalse(dlg.rb_overwrite.isEnabled())
        self.assertIn("暂无历史规则", dlg.rb_overwrite.text())
        self.assertTrue(dlg.rb_new.isChecked())
        self.assertFalse(dlg.overwrite_container.isEnabled())
        self.assertTrue(dlg.new_container.isEnabled())
        self.assertEqual(dlg.combo_plans.count(), 0)

    def test_existing_plans_state_without_current_id(self):
        """测试有历史规则但未指定 current_plan_id：覆盖模式启用，默认进入新建模式但支持切换"""
        mock_plans = [
            {"id": 1, "name": "方案A", "source_db": "db1", "target_db": "db2", "strategy": "diff", "objects_config": [{"name": "t1"}], "updated_at": "2026-09-16 10:00:00"},
            {"id": 2, "name": "方案B", "source_db": "db3", "target_db": "db4", "strategy": "recreate", "objects_config": [], "updated_at": "2026-09-16 11:00:00"}
        ]
        self.mock_storage.get_plans.return_value = mock_plans
        dlg = SavePlanDialog(storage=self.mock_storage, current_plan_id=None)

        self.assertTrue(dlg.rb_overwrite.isEnabled())
        self.assertEqual(dlg.combo_plans.count(), 2)
        # 验证下拉框填充
        self.assertEqual(dlg.combo_plans.itemData(0), 1)
        self.assertEqual(dlg.combo_plans.itemData(1), 2)
        # 验证默认新建
        self.assertTrue(dlg.rb_new.isChecked())
        # 验证参考名称下拉框
        self.assertTrue(hasattr(dlg, "combo_ref_name"))
        self.assertEqual(dlg.combo_ref_name.count(), 3)  # 1 prompt + 2 plans

    def test_existing_plans_with_current_id(self):
        """测试指定了已载入的 current_plan_id：自动切换为覆盖模式并选中该方案"""
        mock_plans = [
            {"id": 10, "name": "ERP财务同步", "source_db": "erp_prod", "target_db": "erp_test", "strategy": "diff", "objects_config": [{"name": "t1"}], "updated_at": "2026-09-16 10:00:00"},
            {"id": 20, "name": "WMS仓储同步", "source_db": "wms_prod", "target_db": "wms_test", "strategy": "recreate", "objects_config": [], "updated_at": "2026-09-16 11:00:00"}
        ]
        self.mock_storage.get_plans.return_value = mock_plans
        dlg = SavePlanDialog(storage=self.mock_storage, current_plan_id=20)

        self.assertTrue(dlg.rb_overwrite.isChecked())
        self.assertEqual(dlg.combo_plans.currentData(), 20)
        self.assertFalse(dlg.plan_detail_card.isHidden())
        self.assertIn("WMS仓储同步", dlg.lbl_detail_target.text())
        self.assertIn("wms_prod", dlg.lbl_detail_db.text())

    def test_mode_toggle(self):
        """测试模式切换时容器启用状态与保存按钮文案"""
        mock_plans = [{"id": 1, "name": "方案A", "source_db": "db1", "target_db": "db2", "strategy": "diff", "objects_config": [], "updated_at": "2026-09-16 10:00:00"}]
        self.mock_storage.get_plans.return_value = mock_plans
        dlg = SavePlanDialog(storage=self.mock_storage, current_plan_id=None)

        # 切换至覆盖模式
        dlg.rb_overwrite.setChecked(True)
        self.assertTrue(dlg.overwrite_container.isEnabled())
        self.assertFalse(dlg.new_container.isEnabled())
        self.assertIn("覆盖", dlg.btn_save.text())

        # 切换至新建模式
        dlg.rb_new.setChecked(True)
        self.assertFalse(dlg.overwrite_container.isEnabled())
        self.assertTrue(dlg.new_container.isEnabled())
        self.assertIn("新规则", dlg.btn_save.text())

    def test_conflict_warning(self):
        """测试新建模式输入已有同名规则时显示警告"""
        mock_plans = [{"id": 1, "name": "已有方案A", "source_db": "db1", "target_db": "db2", "strategy": "diff", "objects_config": [], "updated_at": "2026-09-16 10:00:00"}]
        self.mock_storage.get_plans.return_value = mock_plans
        dlg = SavePlanDialog(storage=self.mock_storage, current_plan_id=None)
        dlg.rb_new.setChecked(True)

        dlg.edit_name.setText("已有方案A")
        self.assertFalse(dlg.lbl_conflict_warn.isHidden())
        self.assertIn("已存在同名规则", dlg.lbl_conflict_warn.text())

        dlg.edit_name.setText("全新方案B")
        self.assertTrue(dlg.lbl_conflict_warn.isHidden())

    def test_ref_name_selection(self):
        """测试参考名称选择后自动填充到编辑框"""
        mock_plans = [{"id": 1, "name": "方案模板A", "source_db": "db1", "target_db": "db2", "strategy": "diff", "objects_config": [], "updated_at": "2026-09-16 10:00:00"}]
        self.mock_storage.get_plans.return_value = mock_plans
        dlg = SavePlanDialog(storage=self.mock_storage, current_plan_id=None)

        dlg.combo_ref_name.setCurrentIndex(1)
        self.assertEqual(dlg.edit_name.text(), "方案模板A")

    @patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes)
    def test_save_overwrite_confirmed(self, mock_question):
        """测试覆盖模式确认后保存成功并返回对应 ID 和名称"""
        mock_plans = [{"id": 5, "name": "方案X", "source_db": "db1", "target_db": "db2", "strategy": "diff", "objects_config": [], "updated_at": "2026-09-16 10:00:00"}]
        self.mock_storage.get_plans.return_value = mock_plans
        dlg = SavePlanDialog(storage=self.mock_storage, current_plan_id=5)

        dlg._on_save()
        self.assertEqual(dlg.result(), QDialog.Accepted)
        plan_id, plan_name = dlg.get_save_target()
        self.assertEqual(plan_id, 5)
        self.assertEqual(plan_name, "方案X")
        mock_question.assert_called_once()

    def test_save_new_unique(self):
        """测试保存全新名称规则成功返回 None 作为 ID"""
        self.mock_storage.get_plans.return_value = []
        dlg = SavePlanDialog(storage=self.mock_storage, current_plan_id=None)

        dlg.edit_name.setText("新建独立方案1")
        dlg._on_save()
        self.assertEqual(dlg.result(), QDialog.Accepted)
        plan_id, plan_name = dlg.get_save_target()
        self.assertIsNone(plan_id)
        self.assertEqual(plan_name, "新建独立方案1")

    @patch.object(QMessageBox, 'information')
    @patch.object(QInputDialog, 'getText', return_value=("新名称_财务同步", True))
    def test_plan_dialog_rename_success(self, mock_input, mock_info):
        """测试在 PlanDialog 中成功重命名方案"""
        dlg = PlanDialog()
        dlg.storage = MagicMock()
        mock_plan = {
            "id": 100,
            "name": "旧名称_财务同步",
            "source_db": "sdb",
            "target_db": "tdb",
            "strategy": "diff",
            "updated_at": "2026-09-16 12:00:00"
        }
        dlg.storage.get_plans.return_value = [mock_plan]
        dlg._load_plans()

        # 选中第 0 行
        dlg.table.selectRow(0)
        dlg._on_rename_selected()

        # 验证调用了 rename_plan
        dlg.storage.rename_plan.assert_called_once_with(100, "新名称_财务同步")
        mock_info.assert_called_once()

    @patch.object(QMessageBox, 'critical')
    @patch.object(QInputDialog, 'getText', return_value=("已存在名称", True))
    def test_plan_dialog_rename_conflict(self, mock_input, mock_crit):
        """测试在 PlanDialog 中重命名为冲突名称时报错提示"""
        dlg = PlanDialog()
        dlg.storage = MagicMock()
        dlg.storage.rename_plan.side_effect = ValueError("已存在同名方案")
        mock_plan = {
            "id": 100,
            "name": "当前方案",
            "source_db": "sdb",
            "target_db": "tdb",
            "strategy": "diff",
            "updated_at": "2026-09-16 12:00:00"
        }
        dlg.storage.get_plans.return_value = [mock_plan]
        dlg._load_plans()

        dlg.table.selectRow(0)
        dlg._on_rename_selected()

        dlg.storage.rename_plan.assert_called_once_with(100, "已存在名称")
        mock_crit.assert_called_once()

    def test_storage_rename_plan_db(self):
        """测试 AppStorage.rename_plan 真实数据库操作与同名冲突抛错"""
        import tempfile
        import os
        from src.core.storage import AppStorage

        # 使用临时文件测试真实 SQLite 操作
        fd, temp_db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            storage = AppStorage(db_path=temp_db)
            p1_id = storage.save_plan({"name": "方案Alpha", "source_db": "db1", "target_db": "db2", "strategy": "diff", "objects_config": []})
            p2_id = storage.save_plan({"name": "方案Beta", "source_db": "db1", "target_db": "db2", "strategy": "diff", "objects_config": []})

            # 1. 成功重命名
            storage.rename_plan(p1_id, "方案Alpha_改")
            p1 = storage.get_plan(p1_id)
            self.assertEqual(p1["name"], "方案Alpha_改")

            # 2. 重命名为已存在的同名方案名称应抛出 ValueError
            with self.assertRaises(ValueError):
                storage.rename_plan(p1_id, "方案Beta")

            # 3. 重命名为自身同名（未冲突）应成功
            storage.rename_plan(p1_id, "方案Alpha_改")
        finally:
            if os.path.exists(temp_db):
                try:
                    os.remove(temp_db)
                except Exception:
                    pass


if __name__ == "__main__":
    unittest.main()
