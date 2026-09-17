"""
绿色免安装便携版一键打包脚本
使用 PyInstaller 生成独立文件夹 (onedir 模式)，所有数据自包含于 ./data 目录中。
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

# 确保在 Windows 控制台下输出 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def build():
    root_dir = Path(__file__).resolve().parent
    dist_dir = root_dir / "dist"
    build_dir = root_dir / "build"
    app_name = "TableMigrateTool"

    print("=== 开始构建绿色单文件便携版 TableMigrateTool ===")

    # 优先检测本地虚拟环境 Python
    python_bin = sys.executable
    venv_python = root_dir / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        venv_python = root_dir / ".venv" / "bin" / "python"
    if venv_python.exists():
        python_bin = str(venv_python)

    icon_path = root_dir / "src" / "gui" / "resources" / "app_icon.ico"
    if not icon_path.exists():
        from src.gui.resources.generate_icons import generate_assets
        generate_assets(root_dir / "src" / "gui" / "resources")

    # 1. 组装 PyInstaller 命令 (--onefile 单文件模式)
    cmd = [
        python_bin, "-m", "PyInstaller",
        "--name", app_name,
        "--onefile",
        "--windowed",
        "--clean",
        "--noconfirm",
        "--icon", str(icon_path),
        "--hidden-import", "pymssql",
        "--hidden-import", "cryptography",
        "--hidden-import", "sqlite3",
        "--hidden-import", "PySide6",
        "--add-data", f"{root_dir / 'src' / 'gui' / 'resources'}{os.pathsep}src/gui/resources",
        str(root_dir / "main.py")
    ]

    print(f"执行命令: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(root_dir))
    if result.returncode != 0:
        print("[ERROR] 打包失败！请检查错误输出。")
        sys.exit(result.returncode)

    # 清理旧 onedir 遗留目录（如果存在），避免混淆
    old_dir = dist_dir / app_name
    if old_dir.is_dir():
        try:
            shutil.rmtree(old_dir)
        except Exception:
            pass

    target_exe = dist_dir / f"{app_name}.exe"

    print("\n[SUCCESS] 绿色单文件版打包构建完成！")
    print(f"独立程序文件: {target_exe}")
    print("【特性说明】")
    print("1. 打包输出仅有单个独立的 exe 程序文件，无 _internal 依赖文件夹。")
    print("2. 程序内已集成自愈与自初始化机制，首次运行时若缺少 data/、data/logs 等目录，将自动在 exe 同级目录创建。")


if __name__ == "__main__":
    build()
