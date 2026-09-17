import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QGridLayout, QScrollArea
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPixmap

from .icons import get_icon, get_pixmap, get_app_icon_path


class ToolCard(QFrame):
    """工具箱单功能交互卡片（紧凑优雅版，支持未来灵活扩展）"""

    clicked = Signal()

    def __init__(
        self,
        icon_name: str,
        icon_color: str,
        badge_text: str,
        badge_color: str,
        title: str,
        subtitle: str,
        features: list,
        btn_text: str,
        btn_primary: bool = True,
        parent=None
    ):
        super().__init__(parent)
        self.setObjectName("ToolCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{title} - {badge_text}\n{subtitle}")
        self._init_ui(icon_name, icon_color, badge_text, badge_color, title, subtitle, features, btn_text, btn_primary)

    def _init_ui(
        self,
        icon_name: str,
        icon_color: str,
        badge_text: str,
        badge_color: str,
        title: str,
        subtitle: str,
        features: list,
        btn_text: str,
        btn_primary: bool
    ):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        # 1. 顶部：图标容器、标题+徽标、进入按钮
        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        # 图标容器 (圆角正方形背景)
        icon_box = QLabel()
        icon_box.setFixedSize(40, 40)
        icon_box.setAlignment(Qt.AlignCenter)
        icon_box.setPixmap(get_pixmap(icon_name, icon_color, 22))
        icon_box.setStyleSheet(f"""
            QLabel {{
                background-color: {icon_color}14;
                border-radius: 8px;
                border: 1px solid {icon_color}25;
            }}
        """)
        top_row.addWidget(icon_box)

        # 标题与徽标
        title_box = QVBoxLayout()
        title_box.setSpacing(3)

        t_row = QHBoxLayout()
        t_row.setSpacing(8)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-size: 15px; font-weight: 700; color: #0f172a;")
        t_row.addWidget(title_lbl)

        if badge_text:
            badge_lbl = QLabel(badge_text)
            badge_lbl.setStyleSheet(f"""
                QLabel {{
                    background-color: {badge_color}14;
                    color: {badge_color};
                    font-size: 11px;
                    font-weight: 600;
                    padding: 2px 7px;
                    border-radius: 6px;
                    border: 1px solid {badge_color}30;
                }}
            """)
            t_row.addWidget(badge_lbl)
        t_row.addStretch()
        title_box.addLayout(t_row)

        # 简要说明文字 (启用自动换行，避免截断)
        sub_lbl = QLabel(subtitle)
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet("font-size: 12px; color: #64748b; line-height: 1.4;")
        title_box.addWidget(sub_lbl)

        top_row.addLayout(title_box, 1)

        # 右侧进入按钮
        btn = QPushButton(btn_text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setIcon(get_icon("arrow_right", "#ffffff" if btn_primary else icon_color, 12))
        btn.setLayoutDirection(Qt.RightToLeft)
        if btn_primary:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {icon_color};
                    color: #ffffff;
                    padding: 6px 14px;
                    font-size: 12px;
                    font-weight: 600;
                    border-radius: 6px;
                    border: none;
                }}
                QPushButton:hover {{
                    background-color: {icon_color}dd;
                }}
            """)
        else:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: {icon_color};
                    padding: 6px 14px;
                    font-size: 12px;
                    font-weight: 600;
                    border-radius: 6px;
                    border: 1px solid {icon_color}50;
                }}
                QPushButton:hover {{
                    background-color: {icon_color}12;
                }}
            """)
        btn.clicked.connect(self.clicked.emit)
        top_row.addWidget(btn)

        layout.addLayout(top_row)

        # 2. 分割线
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #f1f5f9; height: 1px; margin: 1px 0;")
        layout.addWidget(line)

        # 3. 核心亮点 (2x2 栅格，紧凑排布，确保每项文字清晰显示不被遮挡)
        if features:
            feat_grid = QGridLayout()
            feat_grid.setContentsMargins(0, 0, 0, 0)
            feat_grid.setHorizontalSpacing(12)
            feat_grid.setVerticalSpacing(4)
            for i, feat in enumerate(features[:4]):
                r, c = divmod(i, 2)
                f_lbl = QLabel(f"•  {feat}")
                f_lbl.setWordWrap(True)
                f_lbl.setStyleSheet("font-size: 12px; color: #475569;")
                feat_grid.addWidget(f_lbl, r, c)
            layout.addLayout(feat_grid)

        self.setStyleSheet(f"""
            QFrame#ToolCard {{
                background-color: #ffffff;
                border: 1.5px solid #e2e8f0;
                border-radius: 10px;
            }}
            QFrame#ToolCard:hover {{
                border: 1.5px solid {icon_color};
                background-color: #fbfdff;
            }}
        """)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ToolboxWidget(QWidget):
    """工具箱首页聚合面板（紧凑栅格布局，支持多工具扩展与滚动查看）"""

    request_open_migration = Signal()
    request_open_export = Signal()
    request_open_datasource = Signal()
    request_open_migration_plans = Signal()
    request_open_export_plans = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cards = []
        self._init_ui()

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(14)

        # ==================== 1. 顶部 Header ====================
        header_card = QFrame()
        header_card.setObjectName("HeaderCard")
        h_layout = QHBoxLayout(header_card)
        h_layout.setContentsMargins(16, 12, 16, 12)

        # 左侧 Logo 与应用信息
        logo_label = QLabel()
        png_path = get_app_icon_path("png")
        if png_path and os.path.exists(png_path):
            logo_pix = QPixmap(png_path).scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo_label.setPixmap(logo_pix)
        else:
            logo_label.setPixmap(get_pixmap("database", "#2563eb", 28))
        h_layout.addWidget(logo_label)
        h_layout.addSpacing(6)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        lbl_app_title = QLabel("SQL Server 数据库工具箱")
        lbl_app_title.setObjectName("StepTitle")
        lbl_sub = QLabel("跨库结构与数据迁移 ｜ 表结构与数据导出 ｜ 绿色便携")
        lbl_sub.setObjectName("StepDesc")
        lbl_sub.setWordWrap(True)
        lbl_sub.setToolTip("绿色免安装 ｜ 跨库结构与数据迁移 ｜ 灵活表结构与数据导出 ｜ 便携配置")
        title_box.addWidget(lbl_app_title)
        title_box.addWidget(lbl_sub)
        h_layout.addLayout(title_box)

        h_layout.addStretch()

        # 右侧常用配置按钮 (紧凑排布)
        self.btn_manage_ds = QPushButton("数据源配置")
        self.btn_manage_ds.setIcon(get_icon("settings", "#2563eb", 14))
        self.btn_manage_ds.setStyleSheet("padding: 6px 12px; font-size: 13px;")
        self.btn_manage_ds.clicked.connect(self.request_open_datasource.emit)

        self.btn_manage_mig_plans = QPushButton("迁移方案")
        self.btn_manage_mig_plans.setIcon(get_icon("clipboard", "#2563eb", 14))
        self.btn_manage_mig_plans.setStyleSheet("padding: 6px 12px; font-size: 13px;")
        self.btn_manage_mig_plans.clicked.connect(self.request_open_migration_plans.emit)

        self.btn_manage_exp_plans = QPushButton("导出方案")
        self.btn_manage_exp_plans.setIcon(get_icon("archive", "#059669", 14))
        self.btn_manage_exp_plans.setStyleSheet("padding: 6px 12px; font-size: 13px;")
        self.btn_manage_exp_plans.clicked.connect(self.request_open_export_plans.emit)

        h_layout.addWidget(self.btn_manage_ds)
        h_layout.addWidget(self.btn_manage_mig_plans)
        h_layout.addWidget(self.btn_manage_exp_plans)

        root_layout.addWidget(header_card)

        # ==================== 2. 工具栅格区域（带平滑滚动与扩展能力） ====================
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: transparent;
            }
            QScrollArea > QWidget > QWidget {
                background-color: transparent;
            }
        """)

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 4, 0, 8)
        container_layout.setSpacing(14)

        self.grid_layout = QGridLayout()
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setHorizontalSpacing(16)
        self.grid_layout.setVerticalSpacing(16)
        self.grid_layout.setAlignment(Qt.AlignTop)

        # 工具 1：表结构迁移
        self.card_migration = ToolCard(
            icon_name="database",
            icon_color="#2563eb",
            badge_text="跨库同步",
            badge_color="#2563eb",
            title="表结构迁移",
            subtitle="源库与目标库之间的结构对比增量更新、兜底重构与数据同步",
            features=[
                "智能增量对比更新 (ALTER TABLE)",
                "表结构先删后建兜底 (解绑外键)",
                "支持表数据同步与 IDENTITY 维护",
                "视图依赖多轮重试与跨库 SQL 替换"
            ],
            btn_text="进入工具",
            btn_primary=True
        )
        self.card_migration.clicked.connect(self.request_open_migration.emit)

        # 工具 2：表结构导出
        self.card_export = ToolCard(
            icon_name="archive",
            icon_color="#059669",
            badge_text="本地导出",
            badge_color="#059669",
            title="表结构导出",
            subtitle="单独导出表结构、视图定义及表数据脚本，支持单 SQL 与 ZIP 打包",
            features=[
                "支持导出表结构 / 视图 / 表数据",
                "单 SQL 脚本或多表分脚本 ZIP 打包",
                "视图定义在线修改与批量查找替换",
                "导出方案一键保存与随时载入复用"
            ],
            btn_text="进入工具",
            btn_primary=True
        )
        self.card_export.clicked.connect(self.request_open_export.emit)

        self.add_tool_card(self.card_migration)
        self.add_tool_card(self.card_export)

        container_layout.addLayout(self.grid_layout)
        container_layout.addStretch()

        scroll_area.setWidget(container)
        root_layout.addWidget(scroll_area, 1)

        # ==================== 3. 底部状态与提示栏 ====================
        bottom_bar = QHBoxLayout()
        bottom_lbl = QLabel("提示：点击上方任意工具卡片即可进入对应向导，随时可通过向导顶部的“‹ 返回工具箱”按键返回。")
        bottom_lbl.setWordWrap(True)
        bottom_lbl.setStyleSheet("color: #94a3b8; font-size: 12px;")
        bottom_bar.addWidget(bottom_lbl)
        bottom_bar.addStretch()
        root_layout.addLayout(bottom_bar)

    def add_tool_card(self, card: ToolCard, row: int = None, col: int = None):
        """向工具箱栅格中添加工具卡片（默认每行 2 列，支持未来扩展新工具）"""
        if row is None or col is None:
            count = len(self.cards)
            row = count // 2
            col = count % 2
        self.grid_layout.addWidget(card, row, col)
        self.cards.append(card)
