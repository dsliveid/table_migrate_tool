import os
import sys
from datetime import datetime
from typing import Optional, Dict, Any, List

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QStackedWidget, QFrame, QMessageBox,
    QFileDialog, QRadioButton, QButtonGroup, QCheckBox,
    QProgressBar, QPlainTextEdit, QInputDialog, QGridLayout,
    QScrollArea, QDialog
)
from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QTextCursor, QColor, QPixmap

from ..core.config import AppConfig
from ..core.storage import AppStorage
from ..db.connection import MSSQLConnection
from ..db.metadata import DatabaseMetadataExtractor
from ..db.migrator import MigrationEngine, MigrationTaskConfig, MigrationProgressSignal
from .theme import MODERN_STYLE
from .datasource_dialog import DataSourceDialog
from .plan_dialog import PlanDialog, SavePlanDialog
from .widgets import ObjectSelectorTable, DatabaseComboBox
from .icons import get_icon, get_pixmap, get_app_icon, get_app_icon_path
from .toolbox_widget import ToolboxWidget
from .export_widget import ExportWidget




class MigrationWorkerThread(QThread):
    """后台异步迁移执行线程"""
    sig_log = Signal(str, str)  # level, msg
    sig_progress = Signal(int, int, str)  # current, total, text
    sig_finished = Signal(bool, dict)  # success, summary

    def __init__(self, task_config: MigrationTaskConfig):
        super().__init__()
        self.task_config = task_config
        self.engine: Optional[MigrationEngine] = None

    def run(self):
        signals = MigrationProgressSignal()
        signals.log_callback = lambda lvl, msg: self.sig_log.emit(lvl, msg)
        signals.progress_callback = lambda cur, tot, txt: self.sig_progress.emit(cur, tot, txt)
        signals.finished_callback = lambda succ, summ: self.sig_finished.emit(succ, summ)

        self.engine = MigrationEngine(self.task_config, signals)
        self.engine.execute()

    def cancel(self):
        if self.engine:
            self.engine.cancel()


class MainWindow(QMainWindow):
    """迁移工具主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SQL Server 数据库迁移与导出工具箱 (便携版)")
        self.resize(980, 700)
        self.setStyleSheet(MODERN_STYLE)

        # 设置主窗口图标
        icon = get_app_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)

        self.storage = AppStorage()
        self.current_worker: Optional[MigrationWorkerThread] = None

        # 缓存数据
        self.all_datasources: List[Dict[str, Any]] = []
        self.loaded_objects: List[Dict[str, Any]] = []
        self.current_plan_id: Optional[int] = None
        self._loaded_ds_key: Optional[tuple] = None  # (src_ds_id, src_db)

        self._init_ui()
        self._refresh_datasources()

    def _init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        window_layout = QVBoxLayout(central_widget)
        window_layout.setContentsMargins(0, 0, 0, 0)
        window_layout.setSpacing(0)

        # 顶层栈式容器：0-工具箱首页，1-表结构迁移，2-表结构导出
        self.root_stack = QStackedWidget()
        window_layout.addWidget(self.root_stack)

        # ---------------- 页面 0: 工具箱首页 ----------------
        self.toolbox_widget = ToolboxWidget()
        self.toolbox_widget.request_open_migration.connect(lambda: self.root_stack.setCurrentIndex(1))
        self.toolbox_widget.request_open_export.connect(self._on_open_export)
        self.toolbox_widget.request_open_datasource.connect(self._open_datasource_dialog)
        self.toolbox_widget.request_open_migration_plans.connect(self._open_plan_dialog)
        self.toolbox_widget.request_open_export_plans.connect(lambda: self.export_widget._open_plan_dialog())
        self.root_stack.addWidget(self.toolbox_widget)

        # ---------------- 页面 1: 表结构迁移页面 ----------------
        self.migration_page = QWidget()
        root_layout = QVBoxLayout(self.migration_page)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        # ==================== 1. 顶部导航与快捷栏 ====================
        header_card = QFrame()
        header_card.setObjectName("HeaderCard")
        h_layout = QHBoxLayout(header_card)
        h_layout.setContentsMargins(14, 10, 14, 10)

        # 返回工具箱按钮
        self.btn_back_to_toolbox = QPushButton("返回工具箱")
        self.btn_back_to_toolbox.setIcon(get_icon("home", "#2563eb", 14))
        self.btn_back_to_toolbox.setToolTip("返回工具箱主界面")
        self.btn_back_to_toolbox.clicked.connect(lambda: self.root_stack.setCurrentIndex(0))
        h_layout.addWidget(self.btn_back_to_toolbox)
        h_layout.addSpacing(6)

        # 标题与副标题（左侧嵌入专属品牌 Logo 徽标）
        logo_label = QLabel()
        png_path = get_app_icon_path("png")
        if png_path and os.path.exists(png_path):
            logo_pix = QPixmap(png_path).scaled(42, 42, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo_label.setPixmap(logo_pix)
        else:
            logo_label.setPixmap(get_pixmap("database", "#2563eb", 28))
        h_layout.addWidget(logo_label)
        h_layout.addSpacing(4)

        title_box = QVBoxLayout()
        lbl_app_title = QLabel("SQL Server 表结构与数据迁移")
        lbl_app_title.setObjectName("StepTitle")
        lbl_sub = QLabel("绿色免安装 ｜ 增量比对与兜底支持 ｜ 配置随身携带")
        lbl_sub.setObjectName("StepDesc")
        title_box.addWidget(lbl_app_title)
        title_box.addWidget(lbl_sub)
        h_layout.addLayout(title_box)

        h_layout.addStretch()

        # 快捷管理按钮
        self.btn_manage_ds = QPushButton("数据源配置")
        self.btn_manage_ds.setIcon(get_icon("settings", "#2563eb", 15))
        self.btn_manage_ds.clicked.connect(self._open_datasource_dialog)

        self.btn_manage_plans = QPushButton("迁移方案")
        self.btn_manage_plans.setIcon(get_icon("clipboard", "#2563eb", 15))
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
        step_names = ["1. 数据源与库", "2. 筛选对象与规则", "3. 冲突与更新策略", "4. 确认与保存", "5. 执行与监控"]
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
        self._create_step5_widget()

        self._update_step_indicator(0)
        self.root_stack.addWidget(self.migration_page)

        # ---------------- 页面 2: 表结构与数据导出页面 ----------------
        self.export_widget = ExportWidget(storage=self.storage)
        self.export_widget.request_back_to_toolbox.connect(lambda: self.root_stack.setCurrentIndex(0))
        self.root_stack.addWidget(self.export_widget)

        # 默认呈现工具箱主页
        self.root_stack.setCurrentIndex(0)

    # ---------------- 步骤 1: 数据源与数据库选择 ----------------
    def _create_step1_widget(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(16)

        grid_card = QFrame()
        grid_card.setObjectName("CardPanel")
        grid = QGridLayout(grid_card)
        grid.setSpacing(14)

        # 来源端
        lbl_src_title = QLabel("来源端配置 (Source Database)")
        lbl_src_title.setStyleSheet("font-weight: 700; color: #1e40af; font-size: 14px;")
        grid.addWidget(lbl_src_title, 0, 0, 1, 2)

        grid.addWidget(QLabel("来源数据源:"), 1, 0)
        src_ds_layout = QHBoxLayout()
        self.combo_src_ds = QComboBox()
        self.combo_src_ds.currentIndexChanged.connect(self._on_src_ds_changed)
        self.btn_refresh_src_ds = QPushButton()
        self.btn_refresh_src_ds.setObjectName("IconButton")
        self.btn_refresh_src_ds.setIcon(get_icon("refresh", "#2563eb", 14))
        self.btn_refresh_src_ds.setToolTip("刷新数据源列表")
        self.btn_refresh_src_ds.clicked.connect(self._refresh_datasources)
        src_ds_layout.addWidget(self.combo_src_ds, 1)
        src_ds_layout.addWidget(self.btn_refresh_src_ds)
        grid.addLayout(src_ds_layout, 1, 1)

        grid.addWidget(QLabel("来源数据库:"), 2, 0)
        src_db_layout = QHBoxLayout()
        self.combo_src_db = DatabaseComboBox(placeholder="选择已有数据库或输入库名搜索")
        self.btn_refresh_src_db = QPushButton()
        self.btn_refresh_src_db.setObjectName("IconButton")
        self.btn_refresh_src_db.setIcon(get_icon("refresh", "#2563eb", 14))
        self.btn_refresh_src_db.setToolTip("刷新数据库列表")
        self.btn_refresh_src_db.clicked.connect(self._load_src_databases)
        src_db_layout.addWidget(self.combo_src_db, 1)
        src_db_layout.addWidget(self.btn_refresh_src_db)
        grid.addLayout(src_db_layout, 2, 1)

        # 分割
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("color: #e2e8f0; margin: 6px 0;")
        grid.addWidget(line, 3, 0, 1, 2)

        # 目标端
        lbl_tgt_title = QLabel("目标端配置 (Target Database)")
        lbl_tgt_title.setStyleSheet("font-weight: 700; color: #047857; font-size: 14px;")
        grid.addWidget(lbl_tgt_title, 4, 0, 1, 2)

        grid.addWidget(QLabel("目标数据源:"), 5, 0)
        tgt_ds_layout = QHBoxLayout()
        self.combo_tgt_ds = QComboBox()
        self.combo_tgt_ds.currentIndexChanged.connect(self._on_tgt_ds_changed)
        self.btn_refresh_tgt_ds = QPushButton()
        self.btn_refresh_tgt_ds.setObjectName("IconButton")
        self.btn_refresh_tgt_ds.setIcon(get_icon("refresh", "#047857", 14))
        self.btn_refresh_tgt_ds.setToolTip("刷新数据源列表")
        self.btn_refresh_tgt_ds.clicked.connect(self._refresh_datasources)
        tgt_ds_layout.addWidget(self.combo_tgt_ds, 1)
        tgt_ds_layout.addWidget(self.btn_refresh_tgt_ds)
        grid.addLayout(tgt_ds_layout, 5, 1)

        grid.addWidget(QLabel("目标数据库:"), 6, 0)
        tgt_db_layout = QHBoxLayout()
        self.combo_tgt_db = DatabaseComboBox(placeholder="选择已有数据库或直接输入新建库名")
        self.btn_refresh_tgt_db = QPushButton()
        self.btn_refresh_tgt_db.setObjectName("IconButton")
        self.btn_refresh_tgt_db.setIcon(get_icon("refresh", "#047857", 14))
        self.btn_refresh_tgt_db.setToolTip("刷新数据库列表")
        self.btn_refresh_tgt_db.clicked.connect(self._load_tgt_databases)
        tgt_db_layout.addWidget(self.combo_tgt_db, 1)
        tgt_db_layout.addWidget(self.btn_refresh_tgt_db)
        grid.addLayout(tgt_db_layout, 6, 1)

        layout.addWidget(grid_card)

        # 底部导航
        bottom_bar = QHBoxLayout()
        bottom_bar.addStretch()
        self.btn_step1_next = QPushButton("下一步：选择迁移对象")
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
        c_layout.addWidget(self.object_selector)
        layout.addWidget(card, 1)

        # 底部导航
        bottom_bar = QHBoxLayout()
        btn_prev = QPushButton("上一步")
        btn_prev.setIcon(get_icon("arrow_left", "#334155", 14))
        btn_prev.clicked.connect(lambda: self._set_current_step(0))
        bottom_bar.addWidget(btn_prev)

        self.btn_rescan_source = QPushButton("重新扫描源库")
        self.btn_rescan_source.setIcon(get_icon("refresh", "#2563eb", 14))
        self.btn_rescan_source.setToolTip("从源库重新读取表与视图，自动保留您现有的勾选及SQL自定义调整")
        self.btn_rescan_source.clicked.connect(self._on_rescan_step2_objects)
        bottom_bar.addWidget(self.btn_rescan_source)

        bottom_bar.addStretch()

        self.btn_step2_next = QPushButton("下一步：配置冲突策略")
        self.btn_step2_next.setObjectName("PrimaryBtn")
        self.btn_step2_next.setIcon(get_icon("arrow_right", "#ffffff", 14))
        self.btn_step2_next.setLayoutDirection(Qt.RightToLeft)
        self.btn_step2_next.clicked.connect(self._goto_step3)
        bottom_bar.addWidget(self.btn_step2_next)

        layout.addLayout(bottom_bar)
        self.stacked_widget.addWidget(page)

    # ---------------- 步骤 3: 冲突处理与更新策略 ----------------
    def _create_step3_widget(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(14)

        card = QFrame()
        card.setObjectName("CardPanel")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(12)

        lbl_title = QLabel("目标表已存在时的处理策略 (多次重复迁移支持)")
        lbl_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        c_layout.addWidget(lbl_title)

        self.rb_strategy_diff = QRadioButton("策略 A：智能增量对比更新 (Smart Alter / Diff) - 【推荐】")
        self.rb_strategy_diff.setChecked(True)
        lbl_diff_desc = QLabel("    • 自动比对新增列、修改字段长度/类型（ALTER TABLE ADD / ALTER COLUMN）。\n    • 遇主键变更时，若勾选下方自动兜底，将自动触发安全重建。")
        lbl_diff_desc.setStyleSheet("color: #64748b; font-size: 12px; margin-bottom: 6px;")

        self.rb_strategy_recreate = QRadioButton("策略 B：表结构先删后建兜底方案 (Drop and Recreate) - 【强力兜底】")
        lbl_recreate_desc = QLabel("    • 彻底解决主键类型重构、复杂约束冲突等普通 ALTER 无法处理的问题。\n    • 自动解除引用该表的外键约束，执行 DROP TABLE 后全新建立结构。")
        lbl_recreate_desc.setStyleSheet("color: #64748b; font-size: 12px; margin-bottom: 6px;")

        self.rb_strategy_skip = QRadioButton("策略 C：目标表已存在则跳过 (Skip If Exists)")
        lbl_skip_desc = QLabel("    • 目标库已存在的表保持原样，只迁移目标库缺失的新表。")
        lbl_skip_desc.setStyleSheet("color: #64748b; font-size: 12px; margin-bottom: 6px;")

        self.strategy_group = QButtonGroup()
        self.strategy_group.addButton(self.rb_strategy_diff)
        self.strategy_group.addButton(self.rb_strategy_recreate)
        self.strategy_group.addButton(self.rb_strategy_skip)

        c_layout.addWidget(self.rb_strategy_diff)
        c_layout.addWidget(lbl_diff_desc)
        c_layout.addWidget(self.rb_strategy_recreate)
        c_layout.addWidget(lbl_recreate_desc)
        c_layout.addWidget(self.rb_strategy_skip)
        c_layout.addWidget(lbl_skip_desc)

        # 危险警告框（当勾选先删后建时显示）
        self.warning_card = QFrame()
        self.warning_card.setObjectName("WarningCard")
        w_layout = QHBoxLayout(self.warning_card)
        w_layout.setContentsMargins(12, 10, 12, 10)
        w_layout.setSpacing(10)

        warn_icon = QLabel()
        warn_icon.setPixmap(get_pixmap("alert_triangle", "#d97706", 24))
        warn_icon.setAlignment(Qt.AlignTop)

        lbl_warn = QLabel("数据丢失风险提示：\n若选择【先删后建兜底方案】，且对应表设置为【仅迁移结构】（不迁移数据），目标表现存的所有数据将被永久清空！请谨慎选择。")
        lbl_warn.setStyleSheet("color: #b45309; font-weight: 600; line-height: 1.4;")
        w_layout.addWidget(warn_icon)
        w_layout.addWidget(lbl_warn, 1)
        c_layout.addWidget(self.warning_card)

        # 自动兜底复选框
        self.chk_auto_recreate_on_pk = QCheckBox("在智能增量更新时，若检测到主键变动无法直接 ALTER，自动允许执行先删后建兜底")
        self.chk_auto_recreate_on_pk.setChecked(True)
        c_layout.addWidget(self.chk_auto_recreate_on_pk)

        layout.addWidget(card)
        layout.addStretch()

        # 底部导航
        bottom_bar = QHBoxLayout()
        btn_prev = QPushButton("上一步")
        btn_prev.setIcon(get_icon("arrow_left", "#334155", 14))
        btn_prev.clicked.connect(lambda: self._set_current_step(1))
        bottom_bar.addWidget(btn_prev)

        bottom_bar.addStretch()

        self.btn_step3_next = QPushButton("下一步：方案确认与保存")
        self.btn_step3_next.setObjectName("PrimaryBtn")
        self.btn_step3_next.setIcon(get_icon("arrow_right", "#ffffff", 14))
        self.btn_step3_next.setLayoutDirection(Qt.RightToLeft)
        self.btn_step3_next.clicked.connect(self._goto_step4)
        bottom_bar.addWidget(self.btn_step3_next)

        layout.addLayout(bottom_bar)
        self.stacked_widget.addWidget(page)

    # ---------------- 步骤 4: 方案确认与保存 ----------------
    def _create_step4_widget(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(14)

        card = QFrame()
        card.setObjectName("CardPanel")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(12)

        lbl_title = QLabel("本次迁移任务配置总览")
        lbl_title.setStyleSheet("font-weight: bold; font-size: 15px;")
        c_layout.addWidget(lbl_title)

        self.lbl_summary_info = QLabel()
        self.lbl_summary_info.setStyleSheet("line-height: 1.6; font-size: 13px;")
        c_layout.addWidget(self.lbl_summary_info)

        # 方案保存与复用栏
        plan_action_box = QHBoxLayout()
        self.btn_save_plan = QPushButton("保存当前配置为迁移方案/规则（方便后续一键复用）")
        self.btn_save_plan.setIcon(get_icon("save", "#2563eb", 14))
        self.btn_save_plan.clicked.connect(self._on_save_plan)
        plan_action_box.addWidget(self.btn_save_plan)
        plan_action_box.addStretch()
        c_layout.addLayout(plan_action_box)

        layout.addWidget(card)

        # 清单快速预览卡片
        preview_card = QFrame()
        preview_card.setObjectName("CardPanel")
        p_layout = QVBoxLayout(preview_card)
        p_layout.addWidget(QLabel("待迁移对象清单详情："))
        self.txt_preview_objects = QPlainTextEdit()
        self.txt_preview_objects.setReadOnly(True)
        self.txt_preview_objects.setStyleSheet("background-color: #f8fafc; font-family: Consolas, monospace;")
        p_layout.addWidget(self.txt_preview_objects)
        layout.addWidget(preview_card, 1)

        # 底部导航
        bottom_bar = QHBoxLayout()
        btn_prev = QPushButton("上一步")
        btn_prev.setIcon(get_icon("arrow_left", "#334155", 14))
        btn_prev.clicked.connect(lambda: self._set_current_step(2))
        bottom_bar.addWidget(btn_prev)

        bottom_bar.addStretch()

        self.btn_start_migration = QPushButton("确认并开始自动迁移")
        self.btn_start_migration.setObjectName("PrimaryBtn")
        self.btn_start_migration.setIcon(get_icon("rocket", "#ffffff", 16))
        self.btn_start_migration.setStyleSheet("padding: 8px 24px; font-size: 14px; font-weight: 600;")
        self.btn_start_migration.clicked.connect(self._start_migration)
        bottom_bar.addWidget(self.btn_start_migration)

        layout.addLayout(bottom_bar)
        self.stacked_widget.addWidget(page)

    # ---------------- 步骤 5: 执行与实时监控 ----------------
    def _create_step5_widget(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        card = QFrame()
        card.setObjectName("CardPanel")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(10)

        # 状态与进度
        top_status_box = QHBoxLayout()
        top_status_box.setSpacing(8)
        self.lbl_exec_icon = QLabel()
        self.lbl_exec_icon.setPixmap(get_pixmap("rocket", "#2563eb", 18))
        self.lbl_exec_status = QLabel("正在准备执行迁移...")
        self.lbl_exec_status.setStyleSheet("font-size: 14px; font-weight: bold; color: #1e293b;")
        top_status_box.addWidget(self.lbl_exec_icon)
        top_status_box.addWidget(self.lbl_exec_status)
        top_status_box.addStretch()
        c_layout.addLayout(top_status_box)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        c_layout.addWidget(self.progress_bar)

        self.lbl_exec_item = QLabel("")
        self.lbl_exec_item.setStyleSheet("color: #64748b; font-size: 12px;")
        c_layout.addWidget(self.lbl_exec_item)

        layout.addWidget(card)

        # 日志终端
        log_card = QFrame()
        log_card.setObjectName("CardPanel")
        l_layout = QVBoxLayout(log_card)
        l_layout.setSpacing(8)

        log_toolbar = QHBoxLayout()
        log_toolbar.addWidget(QLabel("实时执行日志控制台："))
        log_toolbar.addStretch()

        btn_copy_log = QPushButton("复制日志")
        btn_copy_log.setIcon(get_icon("copy", "#334155", 13))
        btn_copy_log.clicked.connect(self._copy_log)
        btn_export_log = QPushButton("导出日志文件")
        btn_export_log.setIcon(get_icon("download", "#334155", 13))
        btn_export_log.clicked.connect(self._export_log)
        btn_clear_log = QPushButton("清空")
        btn_clear_log.setIcon(get_icon("trash", "#334155", 13))
        btn_clear_log.clicked.connect(self._clear_log)

        log_toolbar.addWidget(btn_copy_log)
        log_toolbar.addWidget(btn_export_log)
        log_toolbar.addWidget(btn_clear_log)
        l_layout.addLayout(log_toolbar)

        self.log_console = QPlainTextEdit()
        self.log_console.setObjectName("LogConsole")
        self.log_console.setReadOnly(True)
        l_layout.addWidget(self.log_console)

        layout.addWidget(log_card, 1)

        # 底部控制栏
        bottom_bar = QHBoxLayout()
        self.btn_cancel_migration = QPushButton("中止迁移")
        self.btn_cancel_migration.setObjectName("DangerBtn")
        self.btn_cancel_migration.setIcon(get_icon("stop", "#ffffff", 14))
        self.btn_cancel_migration.clicked.connect(self._cancel_migration)
        bottom_bar.addWidget(self.btn_cancel_migration)

        bottom_bar.addStretch()

        self.btn_back_to_step2 = QPushButton("返回筛选对象与规则 (步骤2)")
        self.btn_back_to_step2.setIcon(get_icon("undo", "#334155", 14))
        self.btn_back_to_step2.setEnabled(False)
        self.btn_back_to_step2.clicked.connect(lambda: self._set_current_step(1))
        bottom_bar.addWidget(self.btn_back_to_step2)

        self.btn_finish_migration = QPushButton("完成并返回工具箱")
        self.btn_finish_migration.setObjectName("PrimaryBtn")
        self.btn_finish_migration.setIcon(get_icon("home", "#ffffff", 14))
        self.btn_finish_migration.setEnabled(False)
        self.btn_finish_migration.clicked.connect(self._on_finish_migration_to_toolbox)
        bottom_bar.addWidget(self.btn_finish_migration)

        layout.addLayout(bottom_bar)
        self.stacked_widget.addWidget(page)

    # ==================== 逻辑处理与导航 ====================

    def _update_step_indicator(self, current_step: int):
        for i, lbl in enumerate(self.step_labels):
            if i == current_step:
                lbl.setStyleSheet("font-weight: 700; color: #1d4ed8; background-color: #eff6ff; border: 1px solid #bfdbfe; border-radius: 6px; padding: 4px 10px;")
            elif i < current_step:
                lbl.setStyleSheet("font-weight: 600; color: #15803d; background-color: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 6px; padding: 4px 10px;")
            else:
                lbl.setStyleSheet("font-weight: normal; color: #94a3b8; padding: 4px 10px;")

    def _on_finish_migration_to_toolbox(self):
        self._set_current_step(0)
        self.root_stack.setCurrentIndex(0)

    def _on_open_export(self):
        self.root_stack.setCurrentIndex(2)
        if self.export_widget.combo_src_db.count() == 0:
            self.export_widget._load_src_databases()

    def _set_current_step(self, step_idx: int):
        self.stacked_widget.setCurrentIndex(step_idx)
        self._update_step_indicator(step_idx)

    def _refresh_datasources(self):
        """刷新数据源下拉框"""
        self.all_datasources = self.storage.get_datasources()
        
        # 保存当前选中的 ID
        src_id = self.combo_src_ds.currentData()
        tgt_id = self.combo_tgt_ds.currentData()

        self.combo_src_ds.blockSignals(True)
        self.combo_tgt_ds.blockSignals(True)

        self.combo_src_ds.clear()
        self.combo_tgt_ds.clear()

        for ds in self.all_datasources:
            display = f"{ds['name']} ({ds['host']}:{ds['port']})"
            self.combo_src_ds.addItem(display, ds["id"])
            self.combo_tgt_ds.addItem(display, ds["id"])

        self.combo_src_ds.blockSignals(False)
        self.combo_tgt_ds.blockSignals(False)

        # 恢复选择或默认选择第一项与第二项
        if self.all_datasources:
            if src_id:
                idx = self.combo_src_ds.findData(src_id)
                if idx >= 0:
                    self.combo_src_ds.setCurrentIndex(idx)
            else:
                self.combo_src_ds.setCurrentIndex(0)
            self._load_src_databases()

            if tgt_id:
                idx = self.combo_tgt_ds.findData(tgt_id)
                if idx >= 0:
                    self.combo_tgt_ds.setCurrentIndex(idx)
            elif len(self.all_datasources) > 1:
                self.combo_tgt_ds.setCurrentIndex(1)
            else:
                self.combo_tgt_ds.setCurrentIndex(0)
            self._load_tgt_databases()

        if hasattr(self, "export_widget") and self.export_widget:
            self.export_widget._refresh_datasources()

    def _get_selected_ds(self, combo: QComboBox) -> Optional[Dict[str, Any]]:
        ds_id = combo.currentData()
        if not ds_id:
            return None
        for ds in self.all_datasources:
            if ds["id"] == ds_id:
                return ds
        return None

    def _on_src_ds_changed(self):
        self._load_src_databases()

    def _on_tgt_ds_changed(self):
        self._load_tgt_databases()

    def _load_src_databases(self):
        ds = self._get_selected_ds(self.combo_src_ds)
        if not ds:
            self.combo_src_db.set_databases([])
            return
        try:
            with MSSQLConnection(ds, database="master") as conn:
                dbs = conn.get_databases()
                self.combo_src_db.set_databases(dbs)
        except Exception as e:
            self.combo_src_db.set_databases([])
            QMessageBox.warning(self, "连接提示", f"无法获取来源端数据库列表：\n\n{e}")

    def _load_tgt_databases(self):
        ds = self._get_selected_ds(self.combo_tgt_ds)
        if not ds:
            self.combo_tgt_db.set_databases([])
            return
        try:
            with MSSQLConnection(ds, database="master") as conn:
                dbs = conn.get_databases()
                self.combo_tgt_db.set_databases(dbs)
        except Exception as e:
            # 允许手动输入新库名，不阻断，保留当前可能已输入的内容
            self.combo_tgt_db.set_databases([])


    def _load_source_objects(
        self,
        src_ds: Dict[str, Any],
        src_db: str,
        preserve_config: Optional[List[Dict[str, Any]]] = None
    ) -> bool:
        """从源数据库拉取表与视图对象清单并注入到选择器中"""
        try:
            with MSSQLConnection(src_ds, database=src_db) as conn:
                extractor = DatabaseMetadataExtractor(conn)
                objects = extractor.list_objects()
                self.loaded_objects = objects

                def _fetch_view_sql(schema: str, name: str) -> str:
                    with MSSQLConnection(src_ds, database=src_db) as c:
                        ext = DatabaseMetadataExtractor(c)
                        v_meta = ext.extract_view(schema, name)
                        return v_meta.definition

                self.object_selector.set_source_view_fetcher(_fetch_view_sql)
                self.object_selector.load_objects(objects, preserve_config=preserve_config)
                self._loaded_ds_key = (src_ds["id"], src_db)
                return True
        except Exception as e:
            QMessageBox.critical(self, "读取数据库对象失败", f"无法提取数据库 [{src_db}] 元数据: {e}")
            return False

    def _goto_step2(self):
        src_ds = self._get_selected_ds(self.combo_src_ds)
        tgt_ds = self._get_selected_ds(self.combo_tgt_ds)
        src_db = self.combo_src_db.currentText().strip()
        tgt_db = self.combo_tgt_db.currentText().strip()

        if not src_ds or not tgt_ds:
            QMessageBox.warning(self, "提示", "请选择来源和目标数据源！如果尚未配置，请点击右上角【数据源配置】。")
            return
        if not src_db:
            QMessageBox.warning(self, "提示", "请选择来源数据库！")
            return
        if not tgt_db:
            QMessageBox.warning(self, "提示", "请选择或输入目标数据库！")
            return

        current_key = (src_ds["id"], src_db)
        # 如果来源数据源与库未变，且已经载入过对象，完整保留用户已做的所有勾选、模式与SQL调整直接进入步骤2
        if self._loaded_ds_key == current_key and self.object_selector.has_objects():
            self._set_current_step(1)
            return

        if not self._load_source_objects(src_ds, src_db):
            return

        self._set_current_step(1)

    def _on_rescan_step2_objects(self):
        """步骤2中手动重新扫描源库并保留现有规则与SQL自定义"""
        src_ds = self._get_selected_ds(self.combo_src_ds)
        src_db = self.combo_src_db.currentText().strip()
        if not src_ds or not src_db:
            QMessageBox.warning(self, "提示", "请先在第一步选择有效的来源数据源与数据库！")
            return

        current_configs = self.object_selector.get_all_objects_config()
        if self._load_source_objects(src_ds, src_db, preserve_config=current_configs):
            QMessageBox.information(
                self, "重新扫描成功",
                "已从源数据库重新获取最新表与视图列表，并为您完整保留了已有的规则设置与SQL调整！"
            )

    def _goto_step3(self):
        selected = self.object_selector.get_selected_objects()
        if not selected:
            QMessageBox.warning(self, "提示", "请至少勾选一个要迁移的表或视图！")
            return
        self._set_current_step(2)

    def _get_current_strategy(self) -> str:
        if self.rb_strategy_recreate.isChecked():
            return "recreate"
        elif self.rb_strategy_skip.isChecked():
            return "skip"
        return "diff"

    def _goto_step4(self):
        src_ds = self._get_selected_ds(self.combo_src_ds)
        tgt_ds = self._get_selected_ds(self.combo_tgt_ds)
        src_db = self.combo_src_db.currentText().strip()
        tgt_db = self.combo_tgt_db.currentText().strip()
        strategy = self._get_current_strategy()
        selected = self.object_selector.get_selected_objects()

        strat_name = {
            "diff": "智能增量比对更新 (Smart Alter / Diff)",
            "recreate": "表结构先删后建兜底方案 (Drop and Recreate)",
            "skip": "目标已存在则跳过 (Skip If Exists)"
        }[strategy]

        tables = [o for o in selected if o.get("type") == "TABLE"]
        views = [o for o in selected if o.get("type") == "VIEW"]
        tables_with_data = [t for t in tables if t.get("migrate_data")]
        tables_schema_only = [t for t in tables if not t.get("migrate_data")]
        views_with_custom_sql = [v for v in views if v.get("custom_sql")]

        custom_view_info = f"，其中自定义SQL: <b>{len(views_with_custom_sql)}</b> 个" if views_with_custom_sql else ""
        src_str = f"{src_ds['name']} ({src_ds['host']}:{src_ds['port']})" if src_ds else "(未指定)"
        tgt_str = f"{tgt_ds['name']} ({tgt_ds['host']}:{tgt_ds['port']})" if tgt_ds else "(未指定)"
        summary_text = f"""
        <b>来源环境：</b> {src_str} → 数据库: <code>{src_db or '(未指定)'}</code><br>
        <b>目标环境：</b> {tgt_str} → 数据库: <code>{tgt_db or '(未指定)'}</code><br>
        <b>冲突处理策略：</b> <font color="#2563eb"><b>{strat_name}</b></font><br>
        <b>待迁移对象：</b> 共 <b>{len(selected)}</b> 项<br>
        &nbsp;&nbsp;• 普通表: <b>{len(tables)}</b> 张（包含数据: <b>{len(tables_with_data)}</b> 张，仅结构: <b>{len(tables_schema_only)}</b> 张）<br>
        &nbsp;&nbsp;• 视图: <b>{len(views)}</b> 个（<font color="#7c3aed"><b>强制仅迁移结构定义</b></font>{custom_view_info}）
        """
        self.lbl_summary_info.setText(summary_text.strip())

        # 清单文本详情
        lines = []
        lines.append(f"=== 表清单 ({len(tables)} 张) ===")
        for t in tables:
            mode_tag = "[结构+数据]" if t.get("migrate_data") else "[仅结构]"
            lines.append(f"TABLE: [{t['schema']}].[{t['name']}]  {mode_tag}")

        lines.append(f"\n=== 视图清单 ({len(views)} 个) ===")
        for v in views:
            custom_tag = " [自定义SQL]" if v.get("custom_sql") else " [源库定义]"
            lines.append(f"VIEW:  [{v['schema']}].[{v['name']}]  [仅结构]{custom_tag}")

        self.txt_preview_objects.setPlainText("\n".join(lines))
        self._set_current_step(3)

    def _on_save_plan(self):
        """保存当前配置为方案/规则（支持选择并覆盖已有规则）"""
        dlg = SavePlanDialog(storage=self.storage, current_plan_id=self.current_plan_id, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        plan_id, plan_name = dlg.get_save_target()
        if not plan_name:
            return

        src_ds = self._get_selected_ds(self.combo_src_ds)
        tgt_ds = self._get_selected_ds(self.combo_tgt_ds)
        src_db = self.combo_src_db.currentText().strip()
        tgt_db = self.combo_tgt_db.currentText().strip()
        strategy = self._get_current_strategy()
        selected = self.object_selector.get_selected_objects()

        plan_data = {
            "id": plan_id,
            "name": plan_name,
            "source_ds_id": src_ds["id"] if src_ds else None,
            "target_ds_id": tgt_ds["id"] if tgt_ds else None,
            "source_db": src_db,
            "target_db": tgt_db,
            "strategy": strategy,
            "objects_config": selected
        }

        try:
            saved_id = self.storage.save_plan(plan_data)
            self.current_plan_id = saved_id
            QMessageBox.information(
                self, "成功",
                f"迁移方案/规则 [{plan_name}] 已成功保存！随时可在【方案管理】中复用。"
            )
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"保存方案/规则出错: {e}")

    def _open_plan_dialog(self):
        dlg = PlanDialog(self, plan_type="migration")
        dlg.plan_loaded.connect(self._load_saved_plan)
        dlg.exec()

    def _load_saved_plan(self, plan: Dict[str, Any]):
        """从已保存的方案中加载并还原所有配置"""
        self.current_plan_id = plan.get("id")
        self._refresh_datasources()
        
        # 1. 选中数据源
        if plan.get("source_ds_id"):
            idx = self.combo_src_ds.findData(plan["source_ds_id"])
            if idx >= 0:
                self.combo_src_ds.setCurrentIndex(idx)
        if plan.get("target_ds_id"):
            idx = self.combo_tgt_ds.findData(plan["target_ds_id"])
            if idx >= 0:
                self.combo_tgt_ds.setCurrentIndex(idx)

        # 2. 数据库
        self._load_src_databases()
        self._load_tgt_databases()
        src_db_idx = self.combo_src_db.findText(plan.get("source_db", ""))
        if src_db_idx >= 0:
            self.combo_src_db.setCurrentIndex(src_db_idx)
        else:
            self.combo_src_db.setCurrentText(plan.get("source_db", ""))

        tgt_db_idx = self.combo_tgt_db.findText(plan.get("target_db", ""))
        if tgt_db_idx >= 0:
            self.combo_tgt_db.setCurrentIndex(tgt_db_idx)
        else:
            self.combo_tgt_db.setCurrentText(plan.get("target_db", ""))

        # 3. 策略
        strat = plan.get("strategy", "diff")
        if strat == "recreate":
            self.rb_strategy_recreate.setChecked(True)
        elif strat == "skip":
            self.rb_strategy_skip.setChecked(True)
        else:
            self.rb_strategy_diff.setChecked(True)

        # 4. 加载对象列表并恢复选择
        src_ds = self._get_selected_ds(self.combo_src_ds)
        src_db = self.combo_src_db.currentText().strip()
        if src_ds and src_db:
            if self._load_source_objects(src_ds, src_db):
                self.object_selector.restore_selection(plan.get("objects_config", []))

        # 确保切换到迁移页面并直接跳转到确认步骤
        self.root_stack.setCurrentIndex(1)
        self._goto_step4()
        QMessageBox.information(self, "方案已载入", f"已成功载入方案 [{plan.get('name')}]，已为您自动定位到确认页！")

    def _open_datasource_dialog(self):
        dlg = DataSourceDialog(self)
        dlg.exec()
        self._refresh_datasources()
        if hasattr(self, "export_widget") and self.export_widget:
            self.export_widget._refresh_datasources()

    # ---------------- 开始迁移与异步执行 ----------------
    def _start_migration(self):
        strategy = self._get_current_strategy()
        selected = self.object_selector.get_selected_objects()
        has_recreate = (strategy == "recreate")

        # 危险操作确认弹窗
        if has_recreate:
            ans = QMessageBox.warning(
                self, "高风险操作二次确认",
                "您当前选择了【表结构先删后建兜底方案】！\n目标表如果存在，将被 DROP 彻底删除并重建。\n"
                "若未勾选“迁移数据”，目标表现存数据将全部清空且无法恢复！\n\n确定要继续执行吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if ans != QMessageBox.Yes:
                return

        src_ds = self._get_selected_ds(self.combo_src_ds)
        tgt_ds = self._get_selected_ds(self.combo_tgt_ds)
        src_db = self.combo_src_db.currentText().strip()
        tgt_db = self.combo_tgt_db.currentText().strip()

        config = MigrationTaskConfig(
            source_ds=src_ds,
            target_ds=tgt_ds,
            source_db=src_db,
            target_db=tgt_db,
            strategy=strategy,
            objects=selected
        )

        # 界面初始化
        self._set_current_step(4)
        self.progress_bar.setValue(0)
        self.lbl_exec_icon.setPixmap(get_pixmap("rocket", "#2563eb", 18))
        self.lbl_exec_status.setText("迁移任务正在执行中...")
        self.lbl_exec_status.setStyleSheet("font-size: 14px; font-weight: bold; color: #2563eb;")
        self.log_console.clear()
        self.btn_cancel_migration.setEnabled(True)
        self.btn_back_to_step2.setEnabled(False)
        self.btn_finish_migration.setEnabled(False)

        # 启动后台线程
        self.current_worker = MigrationWorkerThread(config)
        self.current_worker.sig_log.connect(self._append_log)
        self.current_worker.sig_progress.connect(self._on_worker_progress)
        self.current_worker.sig_finished.connect(self._on_worker_finished)
        self.current_worker.start()

    def _cancel_migration(self):
        if self.current_worker and self.current_worker.isRunning():
            self.current_worker.cancel()
            self.btn_cancel_migration.setEnabled(False)

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
        self.btn_cancel_migration.setEnabled(False)
        self.btn_back_to_step2.setEnabled(True)
        self.btn_finish_migration.setEnabled(True)

        if success and summary.get("failed_count", 0) == 0:
            self.lbl_exec_icon.setPixmap(get_pixmap("check_circle", "#16a34a", 18))
            self.lbl_exec_status.setText("迁移全部圆满完成！")
            self.lbl_exec_status.setStyleSheet("font-size: 14px; font-weight: bold; color: #16a34a;")
            QMessageBox.information(
                self, "迁移完成",
                f"恭喜！所有选定对象已成功迁移完成。\n"
                f"表: {summary.get('tables_processed', 0)} 张\n"
                f"视图: {summary.get('views_processed', 0)} 个\n"
                f"写入数据行数: {summary.get('total_rows_copied', 0):,} 行\n"
                f"总耗时: {summary.get('elapsed_seconds', 0)} 秒"
            )
        else:
            self.lbl_exec_icon.setPixmap(get_pixmap("alert_triangle", "#dc2626", 18))
            self.lbl_exec_status.setText(f"迁移完成，部分对象存在异常 (失败 {summary.get('failed_count', 0)} 项)")
            self.lbl_exec_status.setStyleSheet("font-size: 14px; font-weight: bold; color: #dc2626;")
            QMessageBox.warning(
                self, "执行告警",
                f"迁移已结束，但有 {summary.get('failed_count', 0)} 个对象失败。\n"
                f"详情请检查下方控制台日志。"
            )

    def _copy_log(self):
        text = self.log_console.toPlainText()
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(text)
        QMessageBox.information(self, "提示", "日志已复制到剪贴板！")

    def _export_log(self):
        text = self.log_console.toPlainText()
        default_name = f"migration_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        default_path = str(AppConfig.get_logs_dir() / default_name)
        
        path, _ = QFileDialog.getSaveFileName(self, "导出迁移日志", default_path, "Log Files (*.log);;Text Files (*.txt)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                QMessageBox.information(self, "成功", f"日志已成功导出至:\n{path}")
            except Exception as e:
                QMessageBox.critical(self, "失败", f"导出日志失败: {e}")

    def _clear_log(self):
        self.log_console.clear()
