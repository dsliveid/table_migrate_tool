import os
import sys
from datetime import datetime
from typing import Optional, Dict, Any, List

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QStackedWidget, QFrame, QMessageBox,
    QFileDialog, QRadioButton, QButtonGroup, QCheckBox,
    QProgressBar, QPlainTextEdit, QGridLayout
)
from PySide6.QtCore import Qt, QThread, Signal, Slot, QUrl
from PySide6.QtGui import QTextCursor, QDesktopServices, QPixmap

from ..core.config import AppConfig
from ..core.storage import AppStorage
from ..db.connection import MSSQLConnection
from ..db.metadata import DatabaseMetadataExtractor
from ..db.exporter import ExportEngine, ExportTaskConfig, ExportProgressSignal
from .datasource_dialog import DataSourceDialog
from .plan_dialog import PlanDialog, SavePlanDialog
from .widgets import ObjectSelectorTable, DatabaseComboBox
from .icons import get_icon, get_pixmap, get_app_icon_path


class ExportWorkerThread(QThread):
    """后台异步导出执行线程"""
    sig_log = Signal(str, str)  # level, msg
    sig_progress = Signal(int, int, str)  # current, total, text
    sig_finished = Signal(bool, dict)  # success, summary

    def __init__(self, task_config: ExportTaskConfig):
        super().__init__()
        self.task_config = task_config
        self.engine: Optional[ExportEngine] = None

    def run(self):
        signals = ExportProgressSignal()
        signals.log_callback = lambda lvl, msg: self.sig_log.emit(lvl, msg)
        signals.progress_callback = lambda cur, tot, txt: self.sig_progress.emit(cur, tot, txt)
        signals.finished_callback = lambda succ, summ: self.sig_finished.emit(succ, summ)

        self.engine = ExportEngine(self.task_config, signals)
        self.engine.execute()

    def cancel(self):
        if self.engine:
            self.engine.cancel()


class ExportWidget(QWidget):
    """表结构与数据导出向导主组件"""

    request_back_to_toolbox = Signal()

    def __init__(self, storage: Optional[AppStorage] = None, parent=None):
        super().__init__(parent)
        self.storage = storage or AppStorage()
        self.current_worker: Optional[ExportWorkerThread] = None

        # 缓存数据
        self.all_datasources: List[Dict[str, Any]] = []
        self.loaded_objects: List[Dict[str, Any]] = []
        self.current_plan_id: Optional[int] = None
        self._loaded_ds_key: Optional[tuple] = None
        self._last_exported_file: Optional[str] = None

        self._init_ui()
        self._refresh_datasources(load_databases=False)

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        # ==================== 1. 顶部 Header ====================
        header_card = QFrame()
        header_card.setObjectName("HeaderCard")
        h_layout = QHBoxLayout(header_card)
        h_layout.setContentsMargins(14, 10, 14, 10)

        # 返回工具箱按钮
        self.btn_back_to_box = QPushButton("返回工具箱")
        self.btn_back_to_box.setIcon(get_icon("home", "#2563eb", 14))
        self.btn_back_to_box.setToolTip("返回工具箱主界面选择其他功能")
        self.btn_back_to_box.clicked.connect(self.request_back_to_toolbox.emit)
        h_layout.addWidget(self.btn_back_to_box)
        h_layout.addSpacing(6)

        # Logo 与标题
        logo_label = QLabel()
        png_path = get_app_icon_path("png")
        if png_path and os.path.exists(png_path):
            logo_pix = QPixmap(png_path).scaled(38, 38, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo_label.setPixmap(logo_pix)
        else:
            logo_label.setPixmap(get_pixmap("archive", "#059669", 28))
        h_layout.addWidget(logo_label)
        h_layout.addSpacing(4)

        title_box = QVBoxLayout()
        lbl_app_title = QLabel("SQL Server 表结构与数据导出")
        lbl_app_title.setObjectName("StepTitle")
        lbl_sub = QLabel("单独导出表结构、视图定义及表数据 ｜ 智能单 SQL 与 ZIP 压缩包输出")
        lbl_sub.setObjectName("StepDesc")
        title_box.addWidget(lbl_app_title)
        title_box.addWidget(lbl_sub)
        h_layout.addLayout(title_box)

        h_layout.addStretch()

        # 快捷管理按钮
        self.btn_manage_ds = QPushButton("数据源配置")
        self.btn_manage_ds.setIcon(get_icon("settings", "#2563eb", 14))
        self.btn_manage_ds.clicked.connect(self._open_datasource_dialog)

        self.btn_manage_plans = QPushButton("导出方案管理")
        self.btn_manage_plans.setIcon(get_icon("archive", "#059669", 14))
        self.btn_manage_plans.clicked.connect(self._open_plan_dialog)

        h_layout.addWidget(self.btn_manage_ds)
        h_layout.addWidget(self.btn_manage_plans)

        root_layout.addWidget(header_card)

        # ==================== 2. 步骤指示器 ====================
        step_indicator_card = QFrame()
        step_indicator_card.setObjectName("CardPanel")
        s_layout = QHBoxLayout(step_indicator_card)
        s_layout.setContentsMargins(12, 8, 12, 8)

        self.step_labels = []
        step_names = ["1. 数据源与库", "2. 筛选对象与规则", "3. 导出选项与保存", "4. 执行与监控"]
        for i, name in enumerate(step_names):
            lbl = QLabel(name)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("font-weight: 500; color: #64748b; padding: 4px 10px;")
            self.step_labels.append(lbl)
            s_layout.addWidget(lbl)
            if i < len(step_names) - 1:
                sep = QLabel("›")
                sep.setStyleSheet("color: #cbd5e1; font-weight: bold; font-size: 16px;")
                s_layout.addWidget(sep)

        root_layout.addWidget(step_indicator_card)

        # ==================== 3. 步骤主内容容器 (QStackedWidget) ====================
        self.stacked_widget = QStackedWidget()
        root_layout.addWidget(self.stacked_widget, 1)

        self._create_step1_widget()
        self._create_step2_widget()
        self._create_step3_widget()
        self._create_step4_widget()

        self._update_step_indicator(0)

    # ---------------- 步骤 1: 数据源与数据库选择 ----------------
    def _create_step1_widget(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(16)

        grid_card = QFrame()
        grid_card.setObjectName("CardPanel")
        grid = QGridLayout(grid_card)
        grid.setSpacing(14)

        lbl_src_title = QLabel("来源数据库配置 (Source Database)")
        lbl_src_title.setStyleSheet("font-weight: 700; color: #047857; font-size: 14px;")
        grid.addWidget(lbl_src_title, 0, 0, 1, 2)

        grid.addWidget(QLabel("选择数据源:"), 1, 0)
        src_ds_layout = QHBoxLayout()
        self.combo_src_ds = QComboBox()
        self.combo_src_ds.currentIndexChanged.connect(self._on_src_ds_changed)
        self.btn_refresh_src_ds = QPushButton()
        self.btn_refresh_src_ds.setObjectName("IconButton")
        self.btn_refresh_src_ds.setIcon(get_icon("refresh", "#059669", 14))
        self.btn_refresh_src_ds.setToolTip("刷新数据源列表")
        self.btn_refresh_src_ds.clicked.connect(self._refresh_datasources)
        src_ds_layout.addWidget(self.combo_src_ds, 1)
        src_ds_layout.addWidget(self.btn_refresh_src_ds)
        grid.addLayout(src_ds_layout, 1, 1)

        grid.addWidget(QLabel("选择数据库:"), 2, 0)
        src_db_layout = QHBoxLayout()
        self.combo_src_db = DatabaseComboBox(placeholder="选择已有数据库或输入库名搜索")
        self.btn_refresh_src_db = QPushButton()
        self.btn_refresh_src_db.setObjectName("IconButton")
        self.btn_refresh_src_db.setIcon(get_icon("refresh", "#059669", 14))
        self.btn_refresh_src_db.setToolTip("刷新数据库列表")
        self.btn_refresh_src_db.clicked.connect(self._load_src_databases)
        src_db_layout.addWidget(self.combo_src_db, 1)
        src_db_layout.addWidget(self.btn_refresh_src_db)
        grid.addLayout(src_db_layout, 2, 1)

        layout.addWidget(grid_card)
        layout.addStretch()

        # 底部导航
        bottom_bar = QHBoxLayout()
        bottom_bar.addStretch()
        self.btn_step1_next = QPushButton("下一步：选择导出对象")
        self.btn_step1_next.setObjectName("PrimaryBtn")
        self.btn_step1_next.setIcon(get_icon("arrow_right", "#ffffff", 14))
        self.btn_step1_next.setLayoutDirection(Qt.RightToLeft)
        self.btn_step1_next.clicked.connect(self._goto_step2)
        bottom_bar.addWidget(self.btn_step1_next)

        layout.addLayout(bottom_bar)
        self.stacked_widget.addWidget(page)

    # ---------------- 步骤 2: 对象筛选与规则设置 ----------------
    def _create_step2_widget(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        card = QFrame()
        card.setObjectName("CardPanel")
        c_layout = QVBoxLayout(card)

        self.object_selector = ObjectSelectorTable()
        self.object_selector.table.setHorizontalHeaderLabels([
            "选择", "对象全名", "对象类型", "估算行数", "导出内容规则", "SQL调整"
        ])
        c_layout.addWidget(self.object_selector)
        layout.addWidget(card, 1)

        # 底部导航
        bottom_bar = QHBoxLayout()
        btn_prev = QPushButton("上一步")
        btn_prev.setIcon(get_icon("arrow_left", "#334155", 14))
        btn_prev.clicked.connect(lambda: self._set_current_step(0))
        bottom_bar.addWidget(btn_prev)

        self.btn_rescan_source = QPushButton("重新扫描源库")
        self.btn_rescan_source.setIcon(get_icon("refresh", "#059669", 14))
        self.btn_rescan_source.setToolTip("从源库重新读取表与视图，自动保留现有勾选及SQL自定义调整")
        self.btn_rescan_source.clicked.connect(self._on_rescan_step2_objects)
        bottom_bar.addWidget(self.btn_rescan_source)

        bottom_bar.addStretch()

        self.btn_step2_next = QPushButton("下一步：配置导出选项")
        self.btn_step2_next.setObjectName("PrimaryBtn")
        self.btn_step2_next.setIcon(get_icon("arrow_right", "#ffffff", 14))
        self.btn_step2_next.setLayoutDirection(Qt.RightToLeft)
        self.btn_step2_next.clicked.connect(self._goto_step3)
        bottom_bar.addWidget(self.btn_step2_next)

        layout.addLayout(bottom_bar)
        self.stacked_widget.addWidget(page)

    # ---------------- 步骤 3: 导出选项与方案确认 ----------------
    def _create_step3_widget(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        # 1. 配置总览卡片
        summary_card = QFrame()
        summary_card.setObjectName("CardPanel")
        s_layout = QVBoxLayout(summary_card)
        s_layout.setSpacing(8)

        lbl_sum_title = QLabel("本次导出配置总览")
        lbl_sum_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        s_layout.addWidget(lbl_sum_title)

        self.lbl_summary_info = QLabel()
        self.lbl_summary_info.setStyleSheet("line-height: 1.5; font-size: 13px;")
        s_layout.addWidget(self.lbl_summary_info)

        plan_action_box = QHBoxLayout()
        self.btn_save_plan = QPushButton("保存当前配置为导出方案/规则（方便后续一键复用）")
        self.btn_save_plan.setIcon(get_icon("save", "#059669", 14))
        self.btn_save_plan.clicked.connect(self._on_save_plan)
        plan_action_box.addWidget(self.btn_save_plan)
        plan_action_box.addStretch()
        s_layout.addLayout(plan_action_box)

        layout.addWidget(summary_card)

        # 2. 打包模式与路径配置卡片
        options_card = QFrame()
        options_card.setObjectName("CardPanel")
        o_layout = QVBoxLayout(options_card)
        o_layout.setSpacing(12)

        lbl_pkg_title = QLabel("导出格式与打包模式")
        lbl_pkg_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        o_layout.addWidget(lbl_pkg_title)

        self.rb_mode_single = QRadioButton("单个 SQL 脚本文件 (.sql)")
        lbl_single_desc = QLabel("    • 所有选定表结构、视图定义及表数据合并写入单个 .sql 文件中。适合纯结构或数据量较小的场景。")
        lbl_single_desc.setStyleSheet("color: #64748b; font-size: 12px;")

        self.rb_mode_zip = QRadioButton("分表独立脚本并合并为 ZIP 压缩包 (.zip)")
        lbl_zip_desc = QLabel("    • 每个表独立生成一个 .sql 脚本（含结构与数据），视图独立生成，并自动合并打包为单个 .zip 压缩包。")
        lbl_zip_desc.setStyleSheet("color: #64748b; font-size: 12px;")

        self.pkg_mode_group = QButtonGroup(self)
        self.pkg_mode_group.addButton(self.rb_mode_single)
        self.pkg_mode_group.addButton(self.rb_mode_zip)
        self.rb_mode_single.toggled.connect(self._on_export_mode_changed)

        o_layout.addWidget(self.rb_mode_single)
        o_layout.addWidget(lbl_single_desc)
        o_layout.addWidget(self.rb_mode_zip)
        o_layout.addWidget(lbl_zip_desc)

        # 路径选择行
        path_box = QHBoxLayout()
        path_box.addWidget(QLabel("导出文件路径:"))
        self.edit_output_path = QPlainTextEdit()
        self.edit_output_path.setFixedHeight(34)
        self.edit_output_path.setStyleSheet("font-family: Consolas, monospace;")
        path_box.addWidget(self.edit_output_path, 1)

        self.btn_browse_path = QPushButton("浏览...")
        self.btn_browse_path.setIcon(get_icon("folder", "#059669", 14))
        self.btn_browse_path.clicked.connect(self._on_browse_output_path)
        path_box.addWidget(self.btn_browse_path)
        o_layout.addLayout(path_box)

        # 高级设置复选框
        adv_box = QHBoxLayout()
        adv_box.setSpacing(14)
        self.chk_drop = QCheckBox("如果存在则删除重建 (DROP IF EXISTS)")
        self.chk_drop.setChecked(True)
        self.chk_drop.setToolTip("若表或视图已存在，则先 DROP 删除再重新创建")

        self.chk_skip = QCheckBox("如果存在则跳过创建 (IF NOT EXISTS)")
        self.chk_skip.setChecked(False)
        self.chk_skip.setToolTip("若表或视图在目标库中已存在，则跳过创建，不影响既有对象")

        self.chk_comments = QCheckBox("包含表与字段中文注释")
        self.chk_comments.setChecked(True)
        self.chk_indexes = QCheckBox("包含主键与辅助索引")
        self.chk_indexes.setChecked(True)

        self.chk_drop.toggled.connect(self._on_chk_drop_toggled)
        self.chk_skip.toggled.connect(self._on_chk_skip_toggled)

        adv_box.addWidget(self.chk_drop)
        adv_box.addWidget(self.chk_skip)
        adv_box.addWidget(self.chk_comments)
        adv_box.addWidget(self.chk_indexes)
        adv_box.addStretch()
        o_layout.addLayout(adv_box)

        layout.addWidget(options_card)

        # 3. 选定对象清单预览
        preview_card = QFrame()
        preview_card.setObjectName("CardPanel")
        p_layout = QVBoxLayout(preview_card)
        p_layout.addWidget(QLabel("待导出对象清单："))
        self.txt_preview_objects = QPlainTextEdit()
        self.txt_preview_objects.setReadOnly(True)
        self.txt_preview_objects.setStyleSheet("background-color: #f8fafc; font-family: Consolas, monospace;")
        p_layout.addWidget(self.txt_preview_objects)
        layout.addWidget(preview_card, 1)

        # 底部导航
        bottom_bar = QHBoxLayout()
        btn_prev = QPushButton("上一步")
        btn_prev.setIcon(get_icon("arrow_left", "#334155", 14))
        btn_prev.clicked.connect(lambda: self._set_current_step(1))
        bottom_bar.addWidget(btn_prev)

        bottom_bar.addStretch()

        self.btn_start_export = QPushButton("确认并开始导出")
        self.btn_start_export.setObjectName("PrimaryBtn")
        self.btn_start_export.setIcon(get_icon("archive", "#ffffff", 15))
        self.btn_start_export.setStyleSheet("padding: 8px 24px; font-size: 14px; font-weight: 600;")
        self.btn_start_export.clicked.connect(self._start_export)
        bottom_bar.addWidget(self.btn_start_export)

        layout.addLayout(bottom_bar)
        self.stacked_widget.addWidget(page)

    # ---------------- 步骤 4: 执行与实时监控 ----------------
    def _create_step4_widget(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        card = QFrame()
        card.setObjectName("CardPanel")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(10)

        # 状态栏
        top_status_box = QHBoxLayout()
        self.lbl_exec_icon = QLabel()
        self.lbl_exec_icon.setPixmap(get_pixmap("archive", "#059669", 18))
        self.lbl_exec_status = QLabel("导出准备就绪")
        self.lbl_exec_status.setStyleSheet("font-size: 14px; font-weight: bold; color: #059669;")
        top_status_box.addWidget(self.lbl_exec_icon)
        top_status_box.addWidget(self.lbl_exec_status)
        top_status_box.addStretch()

        self.btn_open_folder = QPushButton("打开导出文件所在目录")
        self.btn_open_folder.setIcon(get_icon("folder", "#059669", 14))
        self.btn_open_folder.setEnabled(False)
        self.btn_open_folder.clicked.connect(self._open_output_folder)
        top_status_box.addWidget(self.btn_open_folder)

        self.btn_cancel_export = QPushButton("取消导出")
        self.btn_cancel_export.setObjectName("DangerBtn")
        self.btn_cancel_export.setIcon(get_icon("stop", "#ffffff", 13))
        self.btn_cancel_export.clicked.connect(self._cancel_export)
        top_status_box.addWidget(self.btn_cancel_export)
        c_layout.addLayout(top_status_box)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(18)
        self.progress_bar.setTextVisible(True)
        c_layout.addWidget(self.progress_bar)

        self.lbl_exec_item = QLabel("等待执行...")
        self.lbl_exec_item.setStyleSheet("color: #64748b; font-size: 12px;")
        c_layout.addWidget(self.lbl_exec_item)
        layout.addWidget(card)

        # 控制台卡片
        console_card = QFrame()
        console_card.setObjectName("CardPanel")
        con_layout = QVBoxLayout(console_card)
        con_layout.setSpacing(8)

        bar_box = QHBoxLayout()
        bar_box.addWidget(QLabel("导出实时日志与监控输出："))
        bar_box.addStretch()

        btn_copy_log = QPushButton("复制日志")
        btn_copy_log.setIcon(get_icon("copy", "#334155", 13))
        btn_copy_log.clicked.connect(self._copy_log)
        bar_box.addWidget(btn_copy_log)

        btn_export_log = QPushButton("导出日志")
        btn_export_log.setIcon(get_icon("download", "#334155", 13))
        btn_export_log.clicked.connect(self._export_log)
        bar_box.addWidget(btn_export_log)

        btn_clear_log = QPushButton("清空")
        btn_clear_log.setIcon(get_icon("trash", "#64748b", 13))
        btn_clear_log.clicked.connect(self._clear_log)
        bar_box.addWidget(btn_clear_log)
        con_layout.addLayout(bar_box)

        self.log_console = QPlainTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setStyleSheet("""
            QPlainTextEdit {
                background-color: #0f172a;
                color: #f8fafc;
                font-family: Consolas, 'Courier New', monospace;
                font-size: 12px;
                border-radius: 6px;
                padding: 10px;
                line-height: 1.5;
            }
        """)
        con_layout.addWidget(self.log_console, 1)
        layout.addWidget(console_card, 1)

        # 底部导航
        bottom_bar = QHBoxLayout()
        self.btn_back_to_step3 = QPushButton("返回上一步修改")
        self.btn_back_to_step3.setIcon(get_icon("arrow_left", "#334155", 14))
        self.btn_back_to_step3.clicked.connect(lambda: self._set_current_step(2))
        self.btn_back_to_step3.setEnabled(False)
        bottom_bar.addWidget(self.btn_back_to_step3)

        bottom_bar.addStretch()

        self.btn_finish_export = QPushButton("完成并返回工具箱")
        self.btn_finish_export.setObjectName("PrimaryBtn")
        self.btn_finish_export.setIcon(get_icon("home", "#ffffff", 14))
        self.btn_finish_export.clicked.connect(self.request_back_to_toolbox.emit)
        self.btn_finish_export.setEnabled(False)
        bottom_bar.addWidget(self.btn_finish_export)

        layout.addLayout(bottom_bar)
        self.stacked_widget.addWidget(page)

    # ==================== 逻辑联动与步骤控制 ====================

    def _update_step_indicator(self, current_step: int):
        for i, lbl in enumerate(self.step_labels):
            if i == current_step:
                lbl.setStyleSheet("font-weight: 700; color: #059669; background-color: #d1fae5; border-radius: 4px; padding: 4px 10px;")
            elif i < current_step:
                lbl.setStyleSheet("font-weight: 500; color: #10b981; padding: 4px 10px;")
            else:
                lbl.setStyleSheet("font-weight: 500; color: #94a3b8; padding: 4px 10px;")

    def _set_current_step(self, step_idx: int):
        self.stacked_widget.setCurrentIndex(step_idx)
        self._update_step_indicator(step_idx)

    def _refresh_datasources(self, load_databases: bool = False):
        self.all_datasources = self.storage.get_datasources()
        self.combo_src_ds.blockSignals(True)
        self.combo_src_ds.clear()

        for ds in self.all_datasources:
            self.combo_src_ds.addItem(f"{ds['name']} ({ds['host']}:{ds['port']})", ds["id"])

        if self.all_datasources:
            self.combo_src_ds.setCurrentIndex(0)

        self.combo_src_ds.blockSignals(False)

        if self.all_datasources:
            if load_databases:
                self._load_src_databases()
        else:
            self.combo_src_db.clear_items()

    def _get_selected_ds(self, combo: QComboBox) -> Optional[Dict[str, Any]]:
        ds_id = combo.currentData()
        if ds_id is None:
            return None
        return next((ds for ds in self.all_datasources if ds["id"] == ds_id), None)

    def _on_src_ds_changed(self):
        self._load_src_databases()

    def _load_src_databases(self):
        ds = self._get_selected_ds(self.combo_src_ds)
        if not ds:
            self.combo_src_db.set_databases([])
            return

        prev_text = self.combo_src_db.currentText().strip()
        try:
            with MSSQLConnection(ds, database="master") as conn:
                dbs = conn.get_databases()
                self.combo_src_db.set_databases(dbs)
                if prev_text and prev_text in dbs:
                    self.combo_src_db.setCurrentText(prev_text)
                elif dbs:
                    self.combo_src_db.setCurrentText(dbs[0])
        except Exception as e:
            self.combo_src_db.set_databases([])
            QMessageBox.warning(self, "连接失败", f"无法获取来源服务器数据库列表:\n{e}")

    def _fetch_source_view_raw_ddl(self, schema: str, name: str) -> str:
        src_ds = self._get_selected_ds(self.combo_src_ds)
        src_db = self.combo_src_db.currentText().strip()
        if not src_ds or not src_db:
            return ""
        with MSSQLConnection(src_ds, database=src_db) as conn:
            extractor = DatabaseMetadataExtractor(conn)
            view = extractor.extract_view(schema, name)
            return view.definition or ""

    def _goto_step2(self):
        src_ds = self._get_selected_ds(self.combo_src_ds)
        src_db = self.combo_src_db.currentText().strip()

        if not src_ds:
            QMessageBox.warning(self, "提示", "请先选择来源数据源！")
            return
        if not src_db:
            QMessageBox.warning(self, "提示", "请指定来源数据库名称！")
            return

        current_key = (src_ds["id"], src_db)
        if self._loaded_ds_key != current_key or not self.loaded_objects:
            self._load_source_objects(preserve_custom_sql=False)
        else:
            self.object_selector.set_source_view_fetcher(self._fetch_source_view_raw_ddl)

        self._set_current_step(1)

    def _on_rescan_step2_objects(self):
        self._load_source_objects(preserve_custom_sql=True)

    def _load_source_objects(self, preserve_custom_sql: bool = False):
        src_ds = self._get_selected_ds(self.combo_src_ds)
        src_db = self.combo_src_db.currentText().strip()

        prev_config = None
        if preserve_custom_sql:
            prev_config = self.object_selector.get_selected_objects()

        try:
            with MSSQLConnection(src_ds, database=src_db) as conn:
                extractor = DatabaseMetadataExtractor(conn)
                self.loaded_objects = extractor.list_objects()

            self.object_selector.set_source_view_fetcher(self._fetch_source_view_raw_ddl)
            self.object_selector.load_objects(self.loaded_objects, preserve_config=prev_config)
            self._loaded_ds_key = (src_ds["id"], src_db)

            if preserve_custom_sql:
                QMessageBox.information(
                    self, "刷新成功",
                    f"已重新扫描源库 [{src_db}]，发现 {len(self.loaded_objects)} 个对象，原有自定义设置已平滑保留！"
                )
        except Exception as e:
            QMessageBox.critical(self, "扫描失败", f"无法读取源数据库对象列表:\n{e}")

    def _goto_step3(self):
        selected = self.object_selector.get_selected_objects()
        if not selected:
            QMessageBox.warning(self, "提示", "请至少勾选一个要导出的表或视图！")
            return

        src_ds = self._get_selected_ds(self.combo_src_ds)
        src_db = self.combo_src_db.currentText().strip()

        tables = [o for o in selected if o.get("type", "TABLE") == "TABLE"]
        views = [o for o in selected if o.get("type") == "VIEW"]
        tables_with_data = [t for t in tables if bool(t.get("export_data") or t.get("migrate_data", False))]
        tables_schema_only = [t for t in tables if not bool(t.get("export_data") or t.get("migrate_data", False))]
        custom_sql_views = [v for v in views if v.get("custom_sql")]

        # 智能打包模式判断：纯结构默认单 SQL，多表包含数据时默认 ZIP 压缩包
        if len(tables_with_data) > 1:
            self.rb_mode_zip.setChecked(True)
        else:
            self.rb_mode_single.setChecked(True)

        self._update_default_output_path()

        # 更新总览描述
        lines = [
            f"<b>来源端：</b> <code>{src_ds['name']}</code> (库: <code>{src_db}</code>)",
            f"<b>待导出对象总计：</b> <b>{len(selected)}</b> 项",
            f"  • 表: 共 <b>{len(tables)}</b> 张 (仅结构: <b>{len(tables_schema_only)}</b> 张, 结构+数据: <b>{len(tables_with_data)}</b> 张)",
            f"  • 视图: 共 <b>{len(views)}</b> 个 (含自定义调整: <b>{len(custom_sql_views)}</b> 个)"
        ]
        self.lbl_summary_info.setText("<br>".join(lines))

        # 更新清单预览
        preview_text = []
        for obj in selected:
            schema = obj.get("schema", "dbo")
            name = obj.get("name", "")
            otype = obj.get("type", "TABLE")
            with_data = bool(obj.get("export_data") or obj.get("migrate_data", False))
            rule_str = "结构 + 数据" if with_data else "仅结构"
            custom_flag = " [已自定义SQL]" if obj.get("custom_sql") else ""
            preview_text.append(f"[{otype:5s}] [{schema}].[{name}] -> {rule_str}{custom_flag}")

        self.txt_preview_objects.setPlainText("\n".join(preview_text))
        self._set_current_step(2)

    def _on_chk_drop_toggled(self, checked: bool):
        if checked and self.chk_skip.isChecked():
            self.chk_skip.setChecked(False)

    def _on_chk_skip_toggled(self, checked: bool):
        if checked and self.chk_drop.isChecked():
            self.chk_drop.setChecked(False)

    def _on_export_mode_changed(self):
        self._update_default_output_path()

    def _update_default_output_path(self):
        src_db = self.combo_src_db.currentText().strip() or "Database"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        is_zip = self.rb_mode_zip.isChecked()
        ext = "zip" if is_zip else "sql"
        filename = f"Export_{src_db}_{timestamp}.{ext}"
        default_dir = str(AppConfig.get_data_dir())

        current_path = self.edit_output_path.toPlainText().strip()
        if not current_path:
            self.edit_output_path.setPlainText(os.path.join(default_dir, filename))
        else:
            # 自动调整现有路径的扩展名
            base, _ = os.path.splitext(current_path)
            self.edit_output_path.setPlainText(f"{base}.{ext}")

    def _on_browse_output_path(self):
        is_zip = self.rb_mode_zip.isChecked()
        if is_zip:
            filter_str = "ZIP 压缩包 (*.zip);;所有文件 (*.*)"
            default_ext = ".zip"
        else:
            filter_str = "SQL 脚本文件 (*.sql);;所有文件 (*.*)"
            default_ext = ".sql"

        cur = self.edit_output_path.toPlainText().strip()
        path, _ = QFileDialog.getSaveFileName(self, "选择导出文件保存路径", cur, filter_str)
        if path:
            if not path.lower().endswith(default_ext):
                path += default_ext
            self.edit_output_path.setPlainText(path)

    def _on_save_plan(self):
        dlg = SavePlanDialog(
            storage=self.storage,
            current_plan_id=self.current_plan_id,
            parent=self,
            plan_type="export"
        )
        if dlg.exec() != SavePlanDialog.Accepted:
            return

        target_id, target_name = dlg.get_save_target()
        src_ds = self._get_selected_ds(self.combo_src_ds)
        src_db = self.combo_src_db.currentText().strip()
        selected_objects = self.object_selector.get_selected_objects()
        mode = "zip" if self.rb_mode_zip.isChecked() else "single_sql"

        plan_data = {
            "id": target_id,
            "name": target_name,
            "source_ds_id": src_ds["id"] if src_ds else None,
            "source_db": src_db,
            "export_mode": mode,
            "objects_config": {
                "objects": selected_objects,
                "options": {
                    "include_drop": self.chk_drop.isChecked(),
                    "include_skip": self.chk_skip.isChecked(),
                    "include_comments": self.chk_comments.isChecked(),
                    "include_indexes": self.chk_indexes.isChecked()
                }
            }
        }

        try:
            saved_id = self.storage.save_export_plan(plan_data)
            self.current_plan_id = saved_id
            QMessageBox.information(self, "保存成功", f"导出方案【{target_name}】已成功保存！")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"保存导出方案出错: {e}")

    def _open_plan_dialog(self):
        dlg = PlanDialog(parent=self, plan_type="export")
        dlg.plan_loaded.connect(self._load_plan_into_ui)
        dlg.exec()

    def _load_plan_into_ui(self, plan: dict):
        self.current_plan_id = plan.get("id")
        src_ds_id = plan.get("source_ds_id")
        if src_ds_id:
            for idx in range(self.combo_src_ds.count()):
                if self.combo_src_ds.itemData(idx) == src_ds_id:
                    self.combo_src_ds.setCurrentIndex(idx)
                    break

        if plan.get("source_db"):
            self.combo_src_db.setCurrentText(plan["source_db"])

        self._load_source_objects(preserve_custom_sql=False)

        # 按照 plan 中的配置合并并恢复勾选与规则
        raw_config = plan.get("objects_config", [])
        if isinstance(raw_config, dict):
            obj_list = raw_config.get("objects", [])
            opts = raw_config.get("options", {})
            if "include_drop" in opts:
                self.chk_drop.setChecked(bool(opts["include_drop"]))
            if "include_skip" in opts:
                self.chk_skip.setChecked(bool(opts["include_skip"]))
            if "include_comments" in opts:
                self.chk_comments.setChecked(bool(opts["include_comments"]))
            if "include_indexes" in opts:
                self.chk_indexes.setChecked(bool(opts["include_indexes"]))
        else:
            obj_list = raw_config

        self.object_selector.load_objects(self.loaded_objects, preserve_config=obj_list)

        # 恢复模式
        export_mode = plan.get("export_mode", "auto")
        if export_mode == "zip":
            self.rb_mode_zip.setChecked(True)
        elif export_mode == "single_sql":
            self.rb_mode_single.setChecked(True)

        self._goto_step3()
        QMessageBox.information(self, "方案已载入", f"已成功载入导出方案 [{plan.get('name')}]，已为您定位到确认页！")

    def _open_datasource_dialog(self):
        dlg = DataSourceDialog(self)
        dlg.exec()
        self._refresh_datasources()

    # ---------------- 导出执行与异步处理 ----------------
    def _start_export(self):
        output_path = self.edit_output_path.toPlainText().strip()
        if not output_path:
            QMessageBox.warning(self, "提示", "请指定导出保存路径！")
            return

        src_ds = self._get_selected_ds(self.combo_src_ds)
        src_db = self.combo_src_db.currentText().strip()
        selected = self.object_selector.get_selected_objects()
        mode = "zip" if self.rb_mode_zip.isChecked() else "single_sql"

        config = ExportTaskConfig(
            source_ds=src_ds,
            source_db=src_db,
            objects=selected,
            output_path=output_path,
            export_mode=mode,
            batch_size=1000,
            include_drop=self.chk_drop.isChecked(),
            include_skip=self.chk_skip.isChecked(),
            include_comments=self.chk_comments.isChecked(),
            include_indexes=self.chk_indexes.isChecked()
        )

        self._set_current_step(3)
        self.progress_bar.setValue(0)
        self.lbl_exec_icon.setPixmap(get_pixmap("archive", "#059669", 18))
        self.lbl_exec_status.setText("导出任务正在执行中...")
        self.lbl_exec_status.setStyleSheet("font-size: 14px; font-weight: bold; color: #059669;")
        self.log_console.clear()
        self.btn_cancel_export.setEnabled(True)
        self.btn_back_to_step3.setEnabled(False)
        self.btn_finish_export.setEnabled(False)
        self.btn_open_folder.setEnabled(False)
        self._last_exported_file = None

        self.current_worker = ExportWorkerThread(config)
        self.current_worker.sig_log.connect(self._append_log)
        self.current_worker.sig_progress.connect(self._on_worker_progress)
        self.current_worker.sig_finished.connect(self._on_worker_finished)
        self.current_worker.start()

    def _cancel_export(self):
        if self.current_worker and self.current_worker.isRunning():
            self.current_worker.cancel()
            self.btn_cancel_export.setEnabled(False)

    @Slot(str, str)
    def _append_log(self, level: str, msg: str):
        now_str = datetime.now().strftime("%H:%M:%S")
        color_map = {
            "INFO": "#f8fafc",
            "WARN": "#fbbf24",
            "ERROR": "#f87171",
            "DEBUG": "#94a3b8"
        }
        color = color_map.get(level, "#f8fafc")
        html = f"<span style='color: #64748b;'>[{now_str}]</span> <span style='color: {color}; font-weight: bold;'>[{level}]</span> <span style='color: {color};'>{msg}</span>"
        self.log_console.appendHtml(html)
        self.log_console.moveCursor(QTextCursor.End)

    @Slot(int, int, str)
    def _on_worker_progress(self, current: int, total: int, text: str):
        pct = int((current / total) * 100) if total > 0 else 0
        self.progress_bar.setValue(pct)
        self.lbl_exec_item.setText(f"进度: ({current}/{total}) - {text}")

    @Slot(bool, dict)
    def _on_worker_finished(self, success: bool, summary: dict):
        self.progress_bar.setValue(100)
        self.btn_cancel_export.setEnabled(False)
        self.btn_back_to_step3.setEnabled(True)
        self.btn_finish_export.setEnabled(True)
        out_file = summary.get("output_file")
        self._last_exported_file = out_file
        if out_file and os.path.exists(out_file):
            self.btn_open_folder.setEnabled(True)

        if success and summary.get("failed_count", 0) == 0:
            self.lbl_exec_icon.setPixmap(get_pixmap("check_circle", "#16a34a", 18))
            self.lbl_exec_status.setText("导出全部圆满完成！")
            self.lbl_exec_status.setStyleSheet("font-size: 14px; font-weight: bold; color: #16a34a;")

            size_mb = summary.get("file_size_bytes", 0) / (1024 * 1024)
            size_str = f"{size_mb:.2f} MB" if size_mb >= 0.1 else f"{summary.get('file_size_bytes', 0) / 1024:.1f} KB"

            QMessageBox.information(
                self, "导出完成",
                f"恭喜！所有选定对象已成功导出完成。\n\n"
                f"表: {summary.get('tables_processed', 0)} 张\n"
                f"视图: {summary.get('views_processed', 0)} 个\n"
                f"导出数据行数: {summary.get('total_rows_exported', 0):,} 行\n"
                f"生成文件大小: {size_str}\n"
                f"保存路径: {out_file}\n"
                f"总耗时: {summary.get('elapsed_seconds', 0)} 秒"
            )
        else:
            self.lbl_exec_icon.setPixmap(get_pixmap("alert_triangle", "#dc2626", 18))
            self.lbl_exec_status.setText(f"导出完成，部分对象存在异常 (失败 {summary.get('failed_count', 0)} 项)")
            self.lbl_exec_status.setStyleSheet("font-size: 14px; font-weight: bold; color: #dc2626;")
            QMessageBox.warning(
                self, "导出告警",
                f"导出已结束，但有 {summary.get('failed_count', 0)} 个对象失败。\n"
                f"详情请检查下方控制台日志。"
            )

    def _open_output_folder(self):
        if self._last_exported_file and os.path.exists(self._last_exported_file):
            folder = os.path.dirname(os.path.abspath(self._last_exported_file))
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        else:
            QMessageBox.warning(self, "提示", "未找到有效的导出文件或所在目录！")

    def _copy_log(self):
        text = self.log_console.toPlainText()
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(text)
        QMessageBox.information(self, "提示", "日志已复制到剪贴板！")

    def _export_log(self):
        text = self.log_console.toPlainText()
        default_name = f"export_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        default_path = str(AppConfig.get_logs_dir() / default_name)

        path, _ = QFileDialog.getSaveFileName(self, "导出日志", default_path, "Log Files (*.log);;Text Files (*.txt)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                QMessageBox.information(self, "成功", f"日志已成功导出至:\n{path}")
            except Exception as e:
                QMessageBox.critical(self, "失败", f"导出日志失败: {e}")

    def _clear_log(self):
        self.log_console.clear()
