from typing import Optional, Dict, Any, Tuple, List
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QAbstractItemView, QRadioButton, QButtonGroup, QComboBox,
    QLineEdit, QFrame, QWidget, QInputDialog, QMenu
)
from PySide6.QtCore import Qt, Signal
from ..core.storage import AppStorage
from .icons import get_icon


class PlanDialog(QDialog):
    """已保存的方案管理对话框 (支持迁移方案与导出方案)"""
    
    # 方案选择加载信号，向主窗口传递选中的 plan dict
    plan_loaded = Signal(dict)

    def __init__(self, parent=None, plan_type: str = "migration"):
        super().__init__(parent)
        self.plan_type = plan_type
        if self.plan_type == "export":
            self.setWindowTitle("导出方案管理")
        else:
            self.setWindowTitle("迁移方案管理")
        self.resize(800, 440)
        self.storage = AppStorage()

        self._init_ui()
        self._load_plans()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        if self.plan_type == "export":
            top_label = QLabel("已保存的导出方案列表（双击或点击“载入此方案”可在向导中直接复用配置）：")
            layout.addWidget(top_label)

            self.table = QTableWidget()
            self.table.setColumnCount(5)
            self.table.setHorizontalHeaderLabels([
                "ID", "方案名称", "源数据库", "导出模式", "更新时间"
            ])
        else:
            top_label = QLabel("已保存的迁移方案列表（双击或点击“载入此方案”可在向导中直接复用配置）：")
            layout.addWidget(top_label)

            self.table = QTableWidget()
            self.table.setColumnCount(6)
            self.table.setHorizontalHeaderLabels([
                "ID", "方案名称", "源数据库", "目标数据库", "策略", "更新时间"
            ])

        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.doubleClicked.connect(self._on_load_selected)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.table)

        btn_box = QHBoxLayout()
        self.btn_load = QPushButton("载入此方案")
        self.btn_load.setObjectName("PrimaryBtn")
        self.btn_load.setIcon(get_icon("arrow_right", "#ffffff", 14))
        self.btn_load.clicked.connect(self._on_load_selected)

        self.btn_rename = QPushButton("修改名称")
        self.btn_rename.setIcon(get_icon("edit", "#334155", 14))
        self.btn_rename.clicked.connect(self._on_rename_selected)

        self.btn_del = QPushButton("删除方案")
        self.btn_del.setObjectName("DangerBtn")
        self.btn_del.setIcon(get_icon("trash", "#ffffff", 14))
        self.btn_del.clicked.connect(self._on_delete_selected)

        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.accept)

        btn_box.addWidget(self.btn_load)
        btn_box.addWidget(self.btn_rename)
        btn_box.addWidget(self.btn_del)
        btn_box.addStretch()
        btn_box.addWidget(self.btn_close)
        layout.addLayout(btn_box)

    def _load_plans(self):
        self.table.setRowCount(0)
        if self.plan_type == "export":
            plans = self.storage.get_export_plans()
            self.table.setRowCount(len(plans))
            mode_map = {
                "auto": "根据规则选择",
                "single_sql": "单 SQL 文件",
                "zip": "ZIP 压缩包"
            }
            for row, p in enumerate(plans):
                item_id = QTableWidgetItem(str(p["id"]))
                item_id.setData(Qt.UserRole, p)
                self.table.setItem(row, 0, item_id)
                self.table.setItem(row, 1, QTableWidgetItem(p.get("name", "")))
                self.table.setItem(row, 2, QTableWidgetItem(p.get("source_db", "")))
                m_label = mode_map.get(p.get("export_mode", "auto"), p.get("export_mode", "auto"))
                self.table.setItem(row, 3, QTableWidgetItem(m_label))
                self.table.setItem(row, 4, QTableWidgetItem(p.get("updated_at", "")))
        else:
            plans = self.storage.get_plans()
            self.table.setRowCount(len(plans))

            strategy_map = {
                "diff": "智能比对",
                "recreate": "先删后建兜底",
                "skip": "已存在则跳过"
            }

            for row, p in enumerate(plans):
                item_id = QTableWidgetItem(str(p["id"]))
                item_id.setData(Qt.UserRole, p)
                self.table.setItem(row, 0, item_id)
                self.table.setItem(row, 1, QTableWidgetItem(p.get("name", "")))
                self.table.setItem(row, 2, QTableWidgetItem(p.get("source_db", "")))
                self.table.setItem(row, 3, QTableWidgetItem(p.get("target_db", "")))
                strat_label = strategy_map.get(p.get("strategy", ""), p.get("strategy", ""))
                self.table.setItem(row, 4, QTableWidgetItem(strat_label))
                self.table.setItem(row, 5, QTableWidgetItem(p.get("updated_at", "")))

    def _get_selected_plan(self) -> Optional[Dict[str, Any]]:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.data(Qt.UserRole) if item else None

    def _on_load_selected(self):
        plan = self._get_selected_plan()
        if not plan:
            QMessageBox.information(self, "提示", "请先选择一个要载入的方案！")
            return
        self.plan_loaded.emit(plan)
        self.accept()

    def _on_delete_selected(self):
        plan = self._get_selected_plan()
        if not plan:
            QMessageBox.information(self, "提示", "请先选择要删除的方案！")
            return
        ans = QMessageBox.question(
            self, "确认删除",
            f"确定要删除方案 [{plan['name']}] 吗？",
            QMessageBox.Yes | QMessageBox.No
        )
        if ans == QMessageBox.Yes:
            if self.plan_type == "export":
                self.storage.delete_export_plan(plan["id"])
            else:
                self.storage.delete_plan(plan["id"])
            self._load_plans()

    def _on_rename_selected(self):
        plan = self._get_selected_plan()
        if not plan:
            QMessageBox.information(self, "提示", "请先选择要修改名称的方案！")
            return

        current_name = plan.get("name", "")
        new_name, ok = QInputDialog.getText(
            self, "修改方案名称",
            f"请输入方案【{current_name}】的新名称：",
            QLineEdit.Normal,
            current_name
        )
        if not ok:
            return

        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, "提示", "方案名称不能为空！")
            return

        if new_name == current_name:
            return

        try:
            if self.plan_type == "export":
                self.storage.rename_export_plan(plan["id"], new_name)
            else:
                self.storage.rename_plan(plan["id"], new_name)
            QMessageBox.information(self, "成功", f"方案名称已成功修改为【{new_name}】！")
            self._load_plans()
            # 自动重新高亮选中该行
            for r in range(self.table.rowCount()):
                item = self.table.item(r, 0)
                if item and item.text() == str(plan["id"]):
                    self.table.selectRow(r)
                    break
        except Exception as e:
            QMessageBox.critical(self, "修改失败", f"修改方案名称出错: {e}")

    def _show_context_menu(self, pos):
        plan = self._get_selected_plan()
        if not plan:
            return
        menu = QMenu(self)
        act_load = menu.addAction(get_icon("arrow_right", "#2563eb", 14), "载入此方案")
        act_rename = menu.addAction(get_icon("edit", "#334155", 14), "修改方案名称")
        menu.addSeparator()
        act_del = menu.addAction(get_icon("trash", "#dc2626", 14), "删除此方案")

        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == act_load:
            self._on_load_selected()
        elif action == act_rename:
            self._on_rename_selected()
        elif action == act_del:
            self._on_delete_selected()


class SavePlanDialog(QDialog):
    """保存方案/规则对话框（支持迁移方案与导出方案，支持覆盖已有方案或新建方案）"""

    def __init__(
        self,
        storage: Optional[AppStorage] = None,
        current_plan_id: Optional[int] = None,
        parent=None,
        plan_type: str = "migration"
    ):
        super().__init__(parent)
        self.plan_type = plan_type
        if self.plan_type == "export":
            self.setWindowTitle("保存导出方案 / 规则")
        else:
            self.setWindowTitle("保存迁移方案 / 规则")
        self.resize(540, 420)
        self.storage = storage or AppStorage()
        self.current_plan_id = current_plan_id
        self.plans: List[Dict[str, Any]] = []
        self.plans_by_id: Dict[int, Dict[str, Any]] = {}

        self.target_plan_id: Optional[int] = None
        self.target_plan_name: str = ""

        self._load_data()
        self._init_ui()
        self._setup_initial_state()

    def _load_data(self):
        try:
            if self.plan_type == "export":
                self.plans = self.storage.get_export_plans()
            else:
                self.plans = self.storage.get_plans()
        except Exception:
            self.plans = []
        self.plans_by_id = {p["id"]: p for p in self.plans}

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # 顶部说明
        if self.plan_type == "export":
            desc_text = "将当前导出向导中的数据库、筛选对象、结构及数据导出规则保存起来，以便后续快速复用。"
            placeholder_text = "请输入新导出方案名称（如：系统核心表与配置数据备份）"
        else:
            desc_text = "将当前向导中的数据库、筛选对象、结构及数据迁移规则保存起来，以便后续快速复用。"
            placeholder_text = "请输入新规则名称（如：财务系统_日结数据同步）"

        desc_label = QLabel(desc_text)
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: #475569; font-size: 12px; margin-bottom: 2px;")
        layout.addWidget(desc_label)

        # 模式切换卡片
        mode_card = QFrame()
        mode_card.setObjectName("CardPanel")
        mode_layout = QVBoxLayout(mode_card)
        mode_layout.setSpacing(10)

        # 单选按钮组
        self.rb_overwrite = QRadioButton("覆盖已有方案/规则")
        self.rb_new = QRadioButton("另存为新方案/规则")

        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.rb_overwrite)
        self.mode_group.addButton(self.rb_new)
        self.mode_group.buttonToggled.connect(self._on_mode_toggled)

        # ================= 模式 1：覆盖已有规则 =================
        mode_layout.addWidget(self.rb_overwrite)

        self.overwrite_container = QWidget()
        ov_layout = QVBoxLayout(self.overwrite_container)
        ov_layout.setContentsMargins(20, 0, 0, 6)
        ov_layout.setSpacing(6)

        combo_row = QHBoxLayout()
        lbl_sel = QLabel("选择规则:")
        lbl_sel.setFixedWidth(65)
        combo_row.addWidget(lbl_sel)
        self.combo_plans = QComboBox()
        self.combo_plans.setSizeAdjustPolicy(QComboBox.AdjustToContentsOnFirstShow)
        combo_row.addWidget(self.combo_plans, 1)
        ov_layout.addLayout(combo_row)

        # 详情卡片
        self.plan_detail_card = QFrame()
        self.plan_detail_card.setStyleSheet(
            "background-color: #f1f5f9; border-radius: 6px; padding: 8px; border: 1px solid #e2e8f0;"
        )
        detail_layout = QVBoxLayout(self.plan_detail_card)
        detail_layout.setContentsMargins(8, 6, 8, 6)
        detail_layout.setSpacing(4)

        self.lbl_detail_target = QLabel()
        self.lbl_detail_target.setStyleSheet("font-weight: bold; color: #1e293b;")
        self.lbl_detail_db = QLabel()
        self.lbl_detail_db.setStyleSheet("color: #475569; font-size: 12px;")
        self.lbl_detail_meta = QLabel()
        self.lbl_detail_meta.setStyleSheet("color: #64748b; font-size: 11px;")
        self.lbl_detail_warn = QLabel("提示：保存后将使用当前向导配置完全覆盖此规则。")
        self.lbl_detail_warn.setStyleSheet("color: #d97706; font-size: 12px; font-weight: 500;")

        detail_layout.addWidget(self.lbl_detail_target)
        detail_layout.addWidget(self.lbl_detail_db)
        detail_layout.addWidget(self.lbl_detail_meta)
        detail_layout.addWidget(self.lbl_detail_warn)
        ov_layout.addWidget(self.plan_detail_card)

        mode_layout.addWidget(self.overwrite_container)

        # 分割线
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("color: #e2e8f0; margin: 4px 0;")
        mode_layout.addWidget(line)

        # ================= 模式 2：另存为新规则 =================
        mode_layout.addWidget(self.rb_new)

        self.new_container = QWidget()
        new_layout = QVBoxLayout(self.new_container)
        new_layout.setContentsMargins(20, 0, 0, 4)
        new_layout.setSpacing(6)

        name_row = QHBoxLayout()
        lbl_name = QLabel("规则名称:")
        lbl_name.setFixedWidth(65)
        name_row.addWidget(lbl_name)
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText(placeholder_text)
        self.edit_name.textChanged.connect(self._check_name_conflict)
        name_row.addWidget(self.edit_name, 1)
        new_layout.addLayout(name_row)

        # 快捷填入已有名称
        if self.plans:
            ref_row = QHBoxLayout()
            lbl_ref = QLabel("参考名称:")
            lbl_ref.setFixedWidth(65)
            ref_row.addWidget(lbl_ref)
            self.combo_ref_name = QComboBox()
            self.combo_ref_name.addItem("-- 点击选用已有名称作为模板修改 --", "")
            for p in self.plans:
                self.combo_ref_name.addItem(p["name"], p["name"])
            self.combo_ref_name.currentIndexChanged.connect(self._on_ref_name_selected)
            ref_row.addWidget(self.combo_ref_name, 1)
            new_layout.addLayout(ref_row)

        self.lbl_conflict_warn = QLabel()
        self.lbl_conflict_warn.setStyleSheet("color: #d97706; font-size: 12px; font-weight: 500;")
        self.lbl_conflict_warn.setVisible(False)
        new_layout.addWidget(self.lbl_conflict_warn)

        mode_layout.addWidget(self.new_container)
        layout.addWidget(mode_card)

        # 底部按钮栏
        btn_box = QHBoxLayout()
        btn_box.addStretch()

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_save = QPushButton("确定保存")
        self.btn_save.setObjectName("PrimaryBtn")
        self.btn_save.setIcon(get_icon("save", "#ffffff", 14))
        self.btn_save.clicked.connect(self._on_save)

        btn_box.addWidget(self.btn_cancel)
        btn_box.addWidget(self.btn_save)
        layout.addLayout(btn_box)

        # 事件联动
        self.combo_plans.currentIndexChanged.connect(self._on_plan_selection_changed)

    def _setup_initial_state(self):
        if not self.plans:
            self.rb_overwrite.setEnabled(False)
            self.rb_overwrite.setText("覆盖已有方案/规则 (暂无历史规则)")
            self.rb_new.setChecked(True)
            self._on_mode_toggled()
            return

        # 填充下拉列表
        self.combo_plans.blockSignals(True)
        self.combo_plans.clear()
        for p in self.plans:
            src = p.get("source_db") or "-"
            if self.plan_type == "export":
                label = f"{p['name']}  [源库: {src}]"
            else:
                tgt = p.get("target_db") or "-"
                label = f"{p['name']}  [库: {src} → {tgt}]"
            self.combo_plans.addItem(label, p["id"])
        self.combo_plans.blockSignals(False)

        # 若从外部传入了已载入的方案 ID 且存在，则默认选中覆盖该方案
        target_idx = -1
        if self.current_plan_id and self.current_plan_id in self.plans_by_id:
            target_idx = self.combo_plans.findData(self.current_plan_id)

        if target_idx >= 0:
            self.combo_plans.setCurrentIndex(target_idx)
            self.rb_overwrite.setChecked(True)
        else:
            self.combo_plans.setCurrentIndex(0)
            self.rb_new.setChecked(True)

        self._on_mode_toggled()
        self._on_plan_selection_changed()

    def _on_mode_toggled(self):
        is_overwrite = self.rb_overwrite.isChecked()
        self.overwrite_container.setEnabled(is_overwrite)
        self.new_container.setEnabled(not is_overwrite)

        if is_overwrite:
            self.btn_save.setText("覆盖已有规则")
            self.btn_save.setIcon(get_icon("save", "#ffffff", 14))
            self._on_plan_selection_changed()
        else:
            self.btn_save.setText("保存为新规则")
            self.btn_save.setIcon(get_icon("save", "#ffffff", 14))
            self.edit_name.setFocus()
            self._check_name_conflict(self.edit_name.text())

    def _on_plan_selection_changed(self):
        plan_id = self.combo_plans.currentData()
        plan = self.plans_by_id.get(plan_id)
        if plan:
            self.lbl_detail_target.setText(f"目标方案: 【{plan['name']}】")
            src_db = plan.get('source_db') or '(未指定)'
            objs_count = len(plan.get("objects_config") or [])
            updated_at = plan.get("updated_at") or "-"

            if self.plan_type == "export":
                self.lbl_detail_db.setText(f"关联数据库：源库 [{src_db}]")
                mode_map = {"auto": "根据规则选择", "single_sql": "单 SQL 文件", "zip": "ZIP 压缩包"}
                m_label = mode_map.get(plan.get("export_mode", "auto"), plan.get("export_mode", "auto"))
                self.lbl_detail_meta.setText(
                    f"包含对象: {objs_count} 项 | 模式: {m_label} | 最后更新: {updated_at}"
                )
            else:
                tgt_db = plan.get('target_db') or '(未指定)'
                self.lbl_detail_db.setText(f"关联数据库：源库 [{src_db}] → 目标库 [{tgt_db}]")

                strategy_map = {
                    "diff": "智能比对更新",
                    "recreate": "先删后建兜底",
                    "skip": "已存在则跳过"
                }
                strat_label = strategy_map.get(plan.get("strategy", ""), plan.get("strategy", "智能比对"))
                self.lbl_detail_meta.setText(
                    f"包含对象: {objs_count} 项 | 策略: {strat_label} | 最后更新: {updated_at}"
                )
            self.plan_detail_card.setVisible(True)
        else:
            self.plan_detail_card.setVisible(False)

    def _check_name_conflict(self, text: str):
        if not self.rb_new.isChecked():
            self.lbl_conflict_warn.setVisible(False)
            return

        trimmed = text.strip()
        if not trimmed:
            self.lbl_conflict_warn.setVisible(False)
            return

        conflict = next((p for p in self.plans if p["name"].strip().lower() == trimmed.lower()), None)
        if conflict:
            self.lbl_conflict_warn.setText(
                f"提示：检测到已存在同名规则【{conflict['name']}】，点击保存将提示是否覆盖更新。"
            )
            self.lbl_conflict_warn.setVisible(True)
        else:
            self.lbl_conflict_warn.setVisible(False)

    def _on_ref_name_selected(self, index: int):
        if index <= 0 or not hasattr(self, "combo_ref_name"):
            return
        name = self.combo_ref_name.currentData()
        if name:
            self.edit_name.setText(name)
            self.edit_name.setFocus()
            self.edit_name.selectAll()

    def _on_save(self):
        if self.rb_overwrite.isChecked():
            plan_id = self.combo_plans.currentData()
            plan = self.plans_by_id.get(plan_id)
            if not plan:
                QMessageBox.warning(self, "提示", "请先选择要覆盖的已有方案/规则！")
                return

            ans = QMessageBox.question(
                self, "确认覆盖规则",
                f"确定要覆盖已有方案/规则【{plan['name']}】吗？\n\n原规则的所有配置将被当前向导中的配置完全替换。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if ans != QMessageBox.Yes:
                return

            self.target_plan_id = plan["id"]
            self.target_plan_name = plan["name"]
            self.accept()
        else:
            name = self.edit_name.text().strip()
            if not name:
                QMessageBox.warning(self, "提示", "请输入新规则/方案名称！")
                self.edit_name.setFocus()
                return

            # 校验同名冲突
            existing = next((p for p in self.plans if p["name"].strip().lower() == name.lower()), None)
            if existing:
                ans = QMessageBox.question(
                    self, "规则已存在",
                    f"已存在同名方案/规则【{existing['name']}】！\n\n是否直接覆盖更新该规则？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )
                if ans != QMessageBox.Yes:
                    return
                self.target_plan_id = existing["id"]
                self.target_plan_name = existing["name"]
                self.accept()
                return

            self.target_plan_id = None
            self.target_plan_name = name
            self.accept()

    def get_save_target(self) -> Tuple[Optional[int], str]:
        """获取用户选择的保存目标 (plan_id, plan_name)"""
        return self.target_plan_id, self.target_plan_name
