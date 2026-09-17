from typing import List, Dict, Any, Optional, Callable
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox, QAbstractItemView, QMessageBox
)
from PySide6.QtCore import Qt, Signal

from ..view_sql_dialog import ViewSqlDialog, BatchReplaceViewSqlDialog
from ..icons import get_icon


class NoWheelComboBox(QComboBox):
    """禁用鼠标滚轮切换选项的下拉框，防止鼠标滚动浏览表格时误触改变规则"""

    def wheelEvent(self, event):
        event.ignore()


class ObjectSelectorTable(QWidget):
    """表与视图选择与规则配置表格组件"""

    selection_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.raw_objects: List[Dict[str, Any]] = []
        self.objects_data: List[Dict[str, Any]] = []
        self.source_view_fetcher: Optional[Callable[[str, str], str]] = None
        self._init_ui()

    def set_source_view_fetcher(self, fetcher: Optional[Callable[[str, str], str]]):
        """设置从来源数据库按需拉取视图原始 DDL 的回调函数"""
        self.source_view_fetcher = fetcher

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # ---------------- 顶部搜索与快捷操作工具栏 ----------------
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText("输入表名/视图名进行搜索过滤...")
        self.edit_search.addAction(get_icon("search", "#94a3b8", 14), QLineEdit.LeadingPosition)
        self.edit_search.textChanged.connect(self._filter_rows)
        toolbar.addWidget(self.edit_search, 2)

        self.btn_select_all = QPushButton("全选")
        self.btn_select_all.setIcon(get_icon("check_square", "#2563eb", 13))
        self.btn_select_all.clicked.connect(lambda: self._set_all_checked(True))
        toolbar.addWidget(self.btn_select_all)

        self.btn_unselect_all = QPushButton("全不选")
        self.btn_unselect_all.setIcon(get_icon("square", "#64748b", 13))
        self.btn_unselect_all.clicked.connect(lambda: self._set_all_checked(False))
        toolbar.addWidget(self.btn_unselect_all)

        self.btn_only_tables = QPushButton("仅选所有表")
        self.btn_only_tables.setIcon(get_icon("table", "#2563eb", 13))
        self.btn_only_tables.clicked.connect(lambda: self._filter_check_by_type("TABLE"))
        toolbar.addWidget(self.btn_only_tables)

        self.btn_only_views = QPushButton("仅选所有视图")
        self.btn_only_views.setIcon(get_icon("view", "#7c3aed", 13))
        self.btn_only_views.clicked.connect(lambda: self._filter_check_by_type("VIEW"))
        toolbar.addWidget(self.btn_only_views)

        toolbar.addSpacing(10)
        self.btn_all_schema_only = QPushButton("批量: 仅结构")
        self.btn_all_schema_only.clicked.connect(lambda: self._set_all_mode(0))
        toolbar.addWidget(self.btn_all_schema_only)

        self.btn_all_schema_data = QPushButton("批量: 结构+数据")
        self.btn_all_schema_data.clicked.connect(lambda: self._set_all_mode(1))
        toolbar.addWidget(self.btn_all_schema_data)

        toolbar.addSpacing(10)
        self.btn_batch_replace_view_sql = QPushButton("批量替换视图SQL")
        self.btn_batch_replace_view_sql.setIcon(get_icon("replace", "#2563eb", 14))
        self.btn_batch_replace_view_sql.setToolTip("批量查找并替换所有视图中的跨库名、链接服务器 IP 或特定语句")
        self.btn_batch_replace_view_sql.clicked.connect(self._open_batch_replace_dialog)
        toolbar.addWidget(self.btn_batch_replace_view_sql)

        layout.addLayout(toolbar)

        # ---------------- 表格本体 ----------------
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "选择", "对象全名", "对象类型", "估算行数", "迁移内容规则", "SQL调整"
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)

        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table)

        # ---------------- 底部统计信息 ----------------
        self.lbl_stats = QLabel("共 0 项")
        self.lbl_stats.setStyleSheet("color: #64748b; font-weight: 500;")
        layout.addWidget(self.lbl_stats)

    def load_objects(
        self,
        objects: List[Dict[str, Any]],
        preserve_config: Optional[List[Dict[str, Any]]] = None
    ):
        """加载数据库对象列表，默认全选，视图默认强制仅结构。支持按已有配置平滑合并。"""
        self.raw_objects = objects
        self.objects_data = []

        config_map = {}
        if preserve_config:
            config_map = {
                f"[{item.get('schema', 'dbo')}].[{item['name']}]".lower(): item
                for item in preserve_config
            }

        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.setRowCount(len(objects))

        for row, obj in enumerate(objects):
            schema = obj.get("schema", "dbo")
            name = obj.get("name", "")
            obj_type = obj.get("type", "TABLE")
            row_count = obj.get("row_count", 0)

            key = f"[{schema}].[{name}]".lower()
            prev_cfg = config_map.get(key)

            if prev_cfg is not None:
                is_checked = prev_cfg.get("is_checked", True)
                custom_sql = prev_cfg.get("custom_sql")
                migrate_data = bool(prev_cfg.get("migrate_data", False))
            else:
                is_checked = True
                custom_sql = obj.get("custom_sql")
                migrate_data = False

            data_item = {
                "schema": schema,
                "name": name,
                "type": obj_type,
                "row_count": row_count,
                "custom_sql": custom_sql
            }
            self.objects_data.append(data_item)

            # 0. 勾选框
            chk_item = QTableWidgetItem()
            chk_item.setCheckState(Qt.Checked if is_checked else Qt.Unchecked)
            self.table.setItem(row, 0, chk_item)

            # 1. 对象全名
            name_item = QTableWidgetItem(f"[{schema}].[{name}]")
            name_item.setIcon(get_icon("table" if obj_type == "TABLE" else "view", "#2563eb" if obj_type == "TABLE" else "#7c3aed", 14))
            self.table.setItem(row, 1, name_item)

            # 2. 类型标签
            type_item = QTableWidgetItem("表" if obj_type == "TABLE" else "视图")
            type_item.setTextAlignment(Qt.AlignCenter)
            if obj_type == "VIEW":
                type_item.setForeground(Qt.darkMagenta)
            else:
                type_item.setForeground(Qt.darkBlue)
            self.table.setItem(row, 2, type_item)

            # 3. 估算行数
            if obj_type == "TABLE":
                rows_str = f"{row_count:,}" if row_count else "0"
            else:
                rows_str = "-"
            rows_item = QTableWidgetItem(rows_str)
            rows_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 3, rows_item)

            # 4. 迁移内容 ComboBox (禁用鼠标滚轮，防止误触)
            mode_combo = NoWheelComboBox()
            if obj_type == "VIEW":
                # 规则锁定：视图强制仅迁移结构，不可勾选数据
                mode_combo.addItem("仅迁移结构 (视图默认只读)")
                mode_combo.setEnabled(False)
                mode_combo.setToolTip("视图本质为查询逻辑，默认且强制只迁移结构定义")
            else:
                mode_combo.addItem("仅迁移表结构")
                mode_combo.addItem("表结构 ＋ 数据")
                mode_combo.setCurrentIndex(1 if migrate_data else 0)
                mode_combo.currentIndexChanged.connect(lambda: self.selection_changed.emit())

            self.table.setCellWidget(row, 4, mode_combo)

            # 5. SQL 调整列
            if obj_type == "VIEW":
                self._update_view_sql_button(row, data_item)
            else:
                dash_item = QTableWidgetItem("-")
                dash_item.setTextAlignment(Qt.AlignCenter)
                dash_item.setForeground(Qt.gray)
                self.table.setItem(row, 5, dash_item)

        self.table.blockSignals(False)
        self._update_stats()

    def _on_item_changed(self, item: QTableWidgetItem):
        if item.column() == 0:
            self._update_stats()
            self.selection_changed.emit()

    def _set_all_checked(self, checked: bool):
        self.table.blockSignals(True)
        state = Qt.Checked if checked else Qt.Unchecked
        for r in range(self.table.rowCount()):
            if not self.table.isRowHidden(r):
                item = self.table.item(r, 0)
                if item:
                    item.setCheckState(state)
        self.table.blockSignals(False)
        self._update_stats()
        self.selection_changed.emit()

    def _filter_check_by_type(self, target_type: str):
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and r < len(self.objects_data):
                obj = self.objects_data[r]
                if obj and obj.get("type") == target_type:
                    item.setCheckState(Qt.Checked)
                else:
                    item.setCheckState(Qt.Unchecked)
        self.table.blockSignals(False)
        self._update_stats()
        self.selection_changed.emit()

    def _set_all_mode(self, mode_idx: int):
        """批量修改可见表的迁移模式 (0: 仅结构, 1: 结构+数据)"""
        for r in range(self.table.rowCount()):
            if not self.table.isRowHidden(r):
                widget = self.table.cellWidget(r, 4)
                if isinstance(widget, QComboBox) and widget.isEnabled():
                    widget.setCurrentIndex(mode_idx)
        self.selection_changed.emit()

    def _filter_rows(self, text: str):
        filter_text = text.strip().lower()
        for r in range(self.table.rowCount()):
            name_item = self.table.item(r, 1)
            match = (filter_text in name_item.text().lower()) if name_item else True
            self.table.setRowHidden(r, not match)

    def _update_view_sql_button(self, row: int, obj: Dict[str, Any]):
        """根据视图的自定义 SQL 状态更新操作按钮的外观与提示"""
        btn = QPushButton()
        custom_sql = obj.get("custom_sql")
        if custom_sql and custom_sql.strip():
            btn.setText("已自定义 (点击修改)")
            btn.setIcon(get_icon("sparkles", "#15803d", 13))
            btn.setStyleSheet(
                "color: #15803d; font-weight: bold; background-color: #dcfce7; "
                "border: 1px solid #86efac; border-radius: 4px; padding: 3px 8px;"
            )
            btn.setToolTip("当前视图使用了自定义调整后的 SQL 语句。点击可继续编辑或重置为源库默认。")
        else:
            btn.setText("调整SQL")
            btn.setIcon(get_icon("edit", "#4338ca", 13))
            btn.setStyleSheet(
                "color: #4338ca; background-color: #e0e7ff; "
                "border: 1px solid #c7d2fe; border-radius: 4px; padding: 3px 8px;"
            )
            btn.setToolTip("点击查看并在线微调此视图的创建语句（如修改跨库名或链接服务器 IP）")

        btn.clicked.connect(lambda _, r=row: self._open_edit_view_sql(r))
        self.table.setCellWidget(row, 5, btn)

    def _open_edit_view_sql(self, row: int):
        """打开单视图 SQL 编辑对话框"""
        if row >= len(self.objects_data):
            return
        obj = self.objects_data[row]
        schema = obj.get("schema", "dbo")
        name = obj.get("name", "")
        dlg = ViewSqlDialog(
            self,
            schema=schema,
            name=name,
            current_custom_sql=obj.get("custom_sql"),
            fetch_raw_callback=self.source_view_fetcher
        )
        if dlg.exec():
            obj["custom_sql"] = dlg.get_custom_sql()
            self._update_view_sql_button(row, obj)
            self._update_stats()
            self.selection_changed.emit()

    def _open_batch_replace_dialog(self):
        """打开批量文本替换对话框"""
        views_to_process = []
        for r in range(self.table.rowCount()):
            if r >= len(self.objects_data):
                continue
            item = self.table.item(r, 0)
            obj = self.objects_data[r]
            if obj and obj.get("type") == "VIEW":
                is_checked = (item.checkState() == Qt.Checked) if item else True
                view_wrapper = {
                    "schema": obj.get("schema", "dbo"),
                    "name": obj.get("name", ""),
                    "custom_sql": obj.get("custom_sql"),
                    "is_checked": is_checked,
                    "_row": r
                }
                views_to_process.append(view_wrapper)

        if not views_to_process:
            QMessageBox.information(self, "提示", "当前对象列表中没有视图可供调整！")
            return

        dlg = BatchReplaceViewSqlDialog(
            self,
            views_config=views_to_process,
            fetch_raw_callback=self.source_view_fetcher
        )
        dlg.exec()
        # 只要执行过有效替换，无论是点击“关闭”按钮还是右上角“X”，均予以同步保存
        if dlg.replaced_count > 0:
            for vw in views_to_process:
                r = vw["_row"]
                new_sql = vw.get("custom_sql")
                self.objects_data[r]["custom_sql"] = new_sql
                self._update_view_sql_button(r, self.objects_data[r])
            self._update_stats()
            self.selection_changed.emit()

    def _update_stats(self):
        total = self.table.rowCount()
        selected_tables = 0
        selected_tables_with_data = 0
        selected_views = 0
        selected_views_custom = 0

        for r in range(total):
            if r >= len(self.objects_data):
                continue
            item = self.table.item(r, 0)
            if item and item.checkState() == Qt.Checked:
                obj = self.objects_data[r]
                if obj.get("type") == "TABLE":
                    selected_tables += 1
                    combo = self.table.cellWidget(r, 4)
                    if isinstance(combo, QComboBox) and combo.currentIndex() == 1:
                        selected_tables_with_data += 1
                elif obj.get("type") == "VIEW":
                    selected_views += 1
                    if obj.get("custom_sql") and obj.get("custom_sql").strip():
                        selected_views_custom += 1

        selected_total = selected_tables + selected_views
        custom_hint = f" (含自定义SQL: {selected_views_custom} 个)" if selected_views_custom > 0 else ""
        self.lbl_stats.setText(
            f"已选择: {selected_total} / {total} 项 ｜ "
            f"表: {selected_tables} 张 (其中含数据: {selected_tables_with_data} 张) ｜ "
            f"视图: {selected_views} 个 (强制仅结构){custom_hint}"
        )

    def get_selected_objects(self) -> List[Dict[str, Any]]:
        """获取当前勾选的所有对象配置清单"""
        selected = []
        for r in range(self.table.rowCount()):
            if r >= len(self.objects_data):
                continue
            item = self.table.item(r, 0)
            if item and item.checkState() == Qt.Checked:
                obj = self.objects_data[r]
                schema = obj.get("schema", "dbo")
                name = obj.get("name", "")
                obj_type = obj.get("type", "TABLE")
                
                migrate_data = False
                if obj_type == "TABLE":
                    combo = self.table.cellWidget(r, 4)
                    if isinstance(combo, QComboBox):
                        migrate_data = (combo.currentIndex() == 1)
                else:
                    # 视图强制不迁移数据
                    migrate_data = False

                selected.append({
                    "schema": schema,
                    "name": name,
                    "type": obj_type,
                    "migrate_data": migrate_data,
                    "custom_sql": obj.get("custom_sql")
                })
        return selected

    def restore_selection(self, objects_config: List[Dict[str, Any]]):
        """根据保存的方案恢复勾选状态、迁移模式与自定义 SQL"""
        config_map = {f"[{item.get('schema', 'dbo')}].[{item['name']}]".lower(): item for item in objects_config}
        
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            if r >= len(self.objects_data):
                continue
            name_item = self.table.item(r, 1)
            chk_item = self.table.item(r, 0)
            if not name_item or not chk_item:
                continue
            key = name_item.text().lower()
            obj = self.objects_data[r]
            if key in config_map:
                chk_item.setCheckState(Qt.Checked)
                cfg = config_map[key]
                obj["custom_sql"] = cfg.get("custom_sql")
                combo = self.table.cellWidget(r, 4)
                if isinstance(combo, QComboBox) and combo.isEnabled():
                    combo.setCurrentIndex(1 if cfg.get("migrate_data") else 0)
                if obj.get("type") == "VIEW":
                    self._update_view_sql_button(r, obj)
            else:
                chk_item.setCheckState(Qt.Unchecked)
                if obj.get("type") == "VIEW":
                    obj["custom_sql"] = None
                    self._update_view_sql_button(r, obj)
        self.table.blockSignals(False)
        self._update_stats()

    def get_all_objects_config(self) -> List[Dict[str, Any]]:
        """获取当前表格中所有对象的完整配置（包括勾选、模式与自定义SQL）"""
        configs = []
        for r in range(self.table.rowCount()):
            if r >= len(self.objects_data):
                continue
            item = self.table.item(r, 0)
            is_checked = (item.checkState() == Qt.Checked) if item else True
            obj = self.objects_data[r]
            schema = obj.get("schema", "dbo")
            name = obj.get("name", "")
            obj_type = obj.get("type", "TABLE")

            migrate_data = False
            if obj_type == "TABLE":
                combo = self.table.cellWidget(r, 4)
                if isinstance(combo, QComboBox):
                    migrate_data = (combo.currentIndex() == 1)

            configs.append({
                "schema": schema,
                "name": name,
                "type": obj_type,
                "is_checked": is_checked,
                "migrate_data": migrate_data,
                "custom_sql": obj.get("custom_sql")
            })
        return configs

    def has_objects(self) -> bool:
        """判断表格中当前是否已成功加载过对象数据"""
        return len(self.objects_data) > 0 and self.table.rowCount() > 0

