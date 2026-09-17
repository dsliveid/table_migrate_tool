import sys
import os
from pathlib import Path

# 确保将项目根目录加入模块搜索路径
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from src.core.config import AppConfig
from src.gui.main_window import MainWindow
from src.gui.theme import get_app_icon


def main():
    # 0. 在 Windows 平台注册专属 AppUserModelID，确保任务栏正确展示自定义应用图标而非 Python 默认图标
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TableMigrateTool.App.1.0")
        except Exception:
            pass

    # 1. 初始化便携数据目录结构
    AppConfig.initialize()

    # 2. 启用高 DPI 缩放支持
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("TableMigrateTool")
    app.setApplicationDisplayName("SQL Server 数据库工具箱 (迁移与导出)")

    # 全局设置应用程序图标（所有子弹窗和窗口自动继承）
    icon = get_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    # 3. 实例化主窗口并显示
    window = MainWindow()
    if not icon.isNull():
        window.setWindowIcon(icon)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
