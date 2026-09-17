#!/usr/bin/env bash
set -e

# 获取脚本所在根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== 启动 TableMigrateTool ==="

# 检测 Python 解释器 (优先使用虚拟环境)
if [ -f ".venv/Scripts/python.exe" ]; then
    PYTHON=".venv/Scripts/python.exe"
elif [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif command -v python3 &> /dev/null; then
    PYTHON="python3"
elif command -v python &> /dev/null; then
    PYTHON="python"
else
    echo "[ERROR] 未检测到 Python 解释器，请先配置虚拟环境 (.venv) 或安装 Python！"
    exit 1
fi

echo "[INFO] 使用 Python: $($PYTHON --version 2>&1) ($PYTHON)"
echo "[INFO] 正在启动应用程序..."

exec "$PYTHON" main.py "$@"
