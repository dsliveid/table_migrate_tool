import os
import sys
from pathlib import Path

class AppConfig:
    """应用程序便携配置管理类。
    所有数据存放在可执行文件或项目同级目录的 data/ 文件夹中。
    """
    
    @staticmethod
    def get_base_dir() -> Path:
        """获取应用程序根目录（便携模式核心）"""
        if getattr(sys, 'frozen', False):
            # 打包后的 PyInstaller 运行环境（sys.executable 所在目录）
            return Path(sys.executable).resolve().parent
        else:
            # 源码运行环境（src 的上级目录）
            return Path(__file__).resolve().parent.parent.parent

    @classmethod
    def get_data_dir(cls) -> Path:
        """获取便携式数据目录 ./data/"""
        return cls.get_base_dir() / "data"

    @classmethod
    def get_db_path(cls) -> Path:
        """获取本地持久化 SQLite 数据库路径 ./data/app.db"""
        return cls.get_data_dir() / "app.db"

    @classmethod
    def get_logs_dir(cls) -> Path:
        """获取日志存储目录 ./data/logs/"""
        return cls.get_data_dir() / "logs"

    @classmethod
    def get_key_path(cls) -> Path:
        """获取密码加密密钥存储文件 ./data/.secret.key"""
        return cls.get_data_dir() / ".secret.key"

    @classmethod
    def initialize(cls) -> None:
        """初始化目录结构"""
        cls.get_data_dir().mkdir(parents=True, exist_ok=True)
        cls.get_logs_dir().mkdir(parents=True, exist_ok=True)
