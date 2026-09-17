import os
from pathlib import Path
from ..core.config import AppConfig

ARROW_SVG_CONTENT = """<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#64748b" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>"""


def get_down_arrow_path() -> str:
    """获取下拉箭头图标路径，若不存在则自愈生成"""
    try:
        res_dir = Path(__file__).resolve().parent / "resources"
        res_dir.mkdir(parents=True, exist_ok=True)
        res_file = res_dir / "down_arrow.svg"
        if not res_file.exists():
            res_file.write_text(ARROW_SVG_CONTENT, encoding="utf-8")
        return res_file.resolve().as_posix()
    except Exception:
        data_file = AppConfig.get_data_dir() / "down_arrow.svg"
        if not data_file.exists():
            try:
                data_file.parent.mkdir(parents=True, exist_ok=True)
                data_file.write_text(ARROW_SVG_CONTENT, encoding="utf-8")
            except Exception:
                pass
        return data_file.resolve().as_posix()


def get_app_icon_path(extension: str = "ico") -> str:
    """获取应用程序图标文件路径，兼容源码运行及打包环境"""
    try:
        res_file = Path(__file__).resolve().parent / "resources" / f"app_icon.{extension}"
        if res_file.exists():
            return res_file.resolve().as_posix()
    except Exception:
        pass

    # 打包运行环境下的备用路径查找
    alt_file = AppConfig.get_base_dir() / "src" / "gui" / "resources" / f"app_icon.{extension}"
    if alt_file.exists():
        return alt_file.resolve().as_posix()

    return ""


def get_app_icon():
    """获取程序统一的 QIcon 实例"""
    from PySide6.QtGui import QIcon
    ico_path = get_app_icon_path("ico")
    if ico_path and os.path.exists(ico_path):
        icon = QIcon(ico_path)
        if not icon.isNull():
            return icon

    png_path = get_app_icon_path("png")
    if png_path and os.path.exists(png_path):
        icon = QIcon(png_path)
        if not icon.isNull():
            return icon

    return QIcon()


def get_modern_style() -> str:
    """生成带有自适应资源路径的现代专业主题 QSS"""
    arrow_path = get_down_arrow_path()
    return f"""
/* ==================== 全局基础设置 ==================== */
QWidget {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    font-size: 13px;
    color: #1e293b;
    background-color: #f8fafc;
}}

/* ==================== 顶部与卡片容器 ==================== */
QFrame#CardPanel {{
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 14px;
}}

QFrame#HeaderCard {{
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 10px 14px;
}}

QFrame#SourceCard {{
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    border-top: 3px solid #2563eb;
    border-radius: 8px;
    padding: 14px;
}}

QFrame#TargetCard {{
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    border-top: 3px solid #10b981;
    border-radius: 8px;
    padding: 14px;
}}

/* 标题与文字排版 */
QLabel#StepTitle {{
    font-size: 17px;
    font-weight: 700;
    color: #0f172a;
}}

QLabel#StepDesc {{
    font-size: 12px;
    color: #64748b;
}}

QLabel#SectionHeader {{
    font-size: 14px;
    font-weight: 600;
    color: #1e293b;
}}

/* ==================== 按钮样式体系 ==================== */
QPushButton {{
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 6px 14px;
    color: #334155;
    font-weight: 500;
    outline: none;
}}

QPushButton:hover {{
    background-color: #f1f5f9;
    border-color: #94a3b8;
    color: #0f172a;
}}

QPushButton:pressed {{
    background-color: #e2e8f0;
    border-color: #64748b;
}}

QPushButton:disabled {{
    background-color: #f8fafc;
    color: #94a3b8;
    border-color: #e2e8f0;
}}

/* 主操作按钮 (Primary) */
QPushButton#PrimaryBtn {{
    background-color: #2563eb;
    color: #ffffff;
    border: 1px solid #1d4ed8;
    font-weight: 600;
}}

QPushButton#PrimaryBtn:hover {{
    background-color: #1d4ed8;
    border-color: #1e40af;
    color: #ffffff;
}}

QPushButton#PrimaryBtn:pressed {{
    background-color: #1e40af;
    border-color: #1e3a8a;
    color: #ffffff;
}}

QPushButton#PrimaryBtn:disabled {{
    background-color: #93c5fd;
    border-color: #93c5fd;
    color: #eff6ff;
}}

/* 危险/警告按钮 (Danger) */
QPushButton#DangerBtn {{
    background-color: #ef4444;
    color: #ffffff;
    border: 1px solid #dc2626;
    font-weight: 500;
}}

QPushButton#DangerBtn:hover {{
    background-color: #dc2626;
    border-color: #b91c1c;
    color: #ffffff;
}}

QPushButton#DangerBtn:pressed {{
    background-color: #b91c1c;
    border-color: #991b1b;
    color: #ffffff;
}}

QPushButton#DangerBtn:disabled {{
    background-color: #fca5a5;
    border-color: #fca5a5;
    color: #ffffff;
}}

/* 图标专用紧凑按钮 (IconButton) */
QPushButton#IconButton {{
    min-width: 30px;
    max-width: 30px;
    min-height: 30px;
    max-height: 30px;
    padding: 0px;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    background-color: #ffffff;
}}

QPushButton#IconButton:hover {{
    background-color: #eff6ff;
    border-color: #3b82f6;
}}

QPushButton#IconButton:pressed {{
    background-color: #dbeafe;
    border-color: #2563eb;
}}

/* 小型快捷操作按钮 */
QPushButton#SmallBtn {{
    padding: 4px 10px;
    font-size: 12px;
}}

/* ==================== 输入控件与下拉框 ==================== */
QLineEdit, QComboBox, QSpinBox {{
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 5px 10px;
    color: #0f172a;
    min-height: 20px;
}}

QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{
    border-color: #94a3b8;
}}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border: 1.5px solid #2563eb;
    background-color: #ffffff;
}}

QComboBox {{
    padding-right: 28px;
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 26px;
    border-left: 1px solid #e2e8f0;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
    background-color: #f8fafc;
}}

QComboBox::drop-down:hover {{
    background-color: #f1f5f9;
}}

QComboBox::drop-down:pressed {{
    background-color: #e2e8f0;
}}

QComboBox::down-arrow {{
    image: url("{arrow_path}");
    width: 12px;
    height: 12px;
}}

QComboBox QAbstractItemView {{
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 4px;
    selection-background-color: #eff6ff;
    selection-color: #1e40af;
    outline: none;
}}

QComboBox QAbstractItemView::item {{
    min-height: 26px;
    padding: 4px 8px;
    border-radius: 4px;
}}

QComboBox QAbstractItemView::item:hover {{
    background-color: #f1f5f9;
    color: #0f172a;
}}

QComboBox QAbstractItemView::item:selected {{
    background-color: #eff6ff;
    color: #1e40af;
    font-weight: 500;
}}

/* ==================== 表格控件 ==================== */
QTableWidget {{
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    gridline-color: #f1f5f9;
    selection-background-color: #eff6ff;
    selection-color: #1e3a8a;
    alternate-background-color: #fafbfd;
    outline: none;
}}

QTableWidget::item {{
    padding: 4px 8px;
}}

QHeaderView::section {{
    background-color: #f8fafc;
    color: #475569;
    padding: 7px 10px;
    font-weight: 600;
    border: none;
    border-bottom: 2px solid #e2e8f0;
}}

/* ==================== 进度条 ==================== */
QProgressBar {{
    background-color: #e2e8f0;
    border-radius: 6px;
    text-align: center;
    color: #0f172a;
    font-weight: 600;
    font-size: 11px;
    min-height: 16px;
    max-height: 16px;
    border: none;
}}

QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3b82f6, stop:1 #1d4ed8);
    border-radius: 6px;
}}

/* ==================== 日志控制台 ==================== */
QPlainTextEdit#LogConsole {{
    background-color: #0b0f19;
    color: #f8fafc;
    font-family: "Consolas", "Fira Code", "Courier New", monospace;
    font-size: 12px;
    border: 1px solid #1e293b;
    border-radius: 6px;
    padding: 10px;
    line-height: 1.5;
}}

/* ==================== 提示卡片 ==================== */
QFrame#WarningCard {{
    background-color: #fffbeb;
    border: 1px solid #fde68a;
    border-left: 4px solid #f59e0b;
    border-radius: 6px;
    padding: 12px 14px;
}}

QFrame#InfoCard {{
    background-color: #f0f9ff;
    border: 1px solid #bae6fd;
    border-left: 4px solid #0284c7;
    border-radius: 6px;
    padding: 12px 14px;
}}

/* ==================== 现代精致滚动条 ==================== */
QScrollBar:vertical {{
    background: #f8fafc;
    width: 8px;
    margin: 0px;
    border-radius: 4px;
}}

QScrollBar::handle:vertical {{
    background: #cbd5e1;
    min-height: 24px;
    border-radius: 4px;
}}

QScrollBar::handle:vertical:hover {{
    background: #94a3b8;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QScrollBar:horizontal {{
    background: #f8fafc;
    height: 8px;
    margin: 0px;
    border-radius: 4px;
}}

QScrollBar::handle:horizontal {{
    background: #cbd5e1;
    min-width: 24px;
    border-radius: 4px;
}}

QScrollBar::handle:horizontal:hover {{
    background: #94a3b8;
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

/* ==================== 单选框与复选框 ==================== */
QRadioButton, QCheckBox {{
    spacing: 8px;
    color: #1e293b;
}}

QRadioButton::indicator, QCheckBox::indicator {{
    width: 16px;
    height: 16px;
}}

/* ==================== 菜单与弹窗 ==================== */
QMenu {{
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 4px;
}}

QMenu::item {{
    padding: 6px 24px 6px 12px;
    border-radius: 4px;
    color: #1e293b;
}}

QMenu::item:selected {{
    background-color: #eff6ff;
    color: #1d4ed8;
}}

QMenu::separator {{
    height: 1px;
    background: #e2e8f0;
    margin: 4px 6px;
}}

QToolTip {{
    background-color: #1e293b;
    color: #ffffff;
    border: 1px solid #334155;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
}}
"""


MODERN_STYLE = get_modern_style()
