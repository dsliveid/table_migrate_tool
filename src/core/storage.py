import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from .config import AppConfig
from .crypto import PasswordEncryptor


class AppStorage:
    """本地持久化 SQLite 存储管理"""

    def __init__(self, db_path: Optional[str] = None):
        AppConfig.initialize()
        self.db_path = str(db_path) if db_path else str(AppConfig.get_db_path())
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1. 数据源连接表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS datasources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER DEFAULT 1433,
                    auth_type TEXT DEFAULT 'sql', -- 'sql' or 'windows'
                    username TEXT,
                    password TEXT,                -- 加密存储
                    extra_params TEXT,            -- JSON 格式存储高级参数
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            
            # 2. 迁移方案表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS migration_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT,
                    source_ds_id INTEGER,
                    target_ds_id INTEGER,
                    source_db TEXT NOT NULL,
                    target_db TEXT NOT NULL,
                    strategy TEXT DEFAULT 'diff', -- 'diff', 'recreate', 'skip'
                    objects_config TEXT NOT NULL, -- JSON 格式存储选定对象列表和规则
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # 3. 导出方案表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS export_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT,
                    source_ds_id INTEGER,
                    source_db TEXT NOT NULL,
                    export_mode TEXT DEFAULT 'auto', -- 'auto', 'single_sql', 'zip'
                    objects_config TEXT NOT NULL,    -- JSON 格式存储选定对象列表和规则
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.commit()

    # ==================== 数据源 CRUD ====================

    def get_datasources(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM datasources ORDER BY id ASC")
            rows = cursor.fetchall()
            result = []
            for row in rows:
                item = dict(row)
                if item.get("password"):
                    item["password"] = PasswordEncryptor.decrypt(item["password"])
                if item.get("extra_params"):
                    try:
                        item["extra_params"] = json.loads(item["extra_params"])
                    except Exception:
                        item["extra_params"] = {}
                else:
                    item["extra_params"] = {}
                result.append(item)
            return result

    def get_datasource(self, ds_id: int) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM datasources WHERE id = ?", (ds_id,))
            row = cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            if item.get("password"):
                item["password"] = PasswordEncryptor.decrypt(item["password"])
            if item.get("extra_params"):
                try:
                    item["extra_params"] = json.loads(item["extra_params"])
                except Exception:
                    item["extra_params"] = {}
            else:
                item["extra_params"] = {}
            return item

    def save_datasource(self, data: Dict[str, Any]) -> int:
        """保存或更新数据源。如果包含 id 则更新，否则新增。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        password = data.get("password", "")
        enc_password = PasswordEncryptor.encrypt(password) if password else ""
        extra_json = json.dumps(data.get("extra_params", {}), ensure_ascii=False)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            ds_id = data.get("id")
            if ds_id:
                cursor.execute("""
                    UPDATE datasources
                    SET name = ?, host = ?, port = ?, auth_type = ?, username = ?, 
                        password = ?, extra_params = ?, updated_at = ?
                    WHERE id = ?
                """, (
                    data["name"], data["host"], data.get("port", 1433),
                    data.get("auth_type", "sql"), data.get("username", ""),
                    enc_password, extra_json, now, ds_id
                ))
                conn.commit()
                return ds_id
            else:
                cursor.execute("""
                    INSERT INTO datasources (name, host, port, auth_type, username, password, extra_params, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    data["name"], data["host"], data.get("port", 1433),
                    data.get("auth_type", "sql"), data.get("username", ""),
                    enc_password, extra_json, now, now
                ))
                conn.commit()
                return cursor.lastrowid

    def delete_datasource(self, ds_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM datasources WHERE id = ?", (ds_id,))
            conn.commit()
            return cursor.rowcount > 0

    # ==================== 迁移方案 CRUD ====================

    def get_plans(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM migration_plans ORDER BY updated_at DESC")
            rows = cursor.fetchall()
            result = []
            for row in rows:
                item = dict(row)
                if item.get("objects_config"):
                    try:
                        item["objects_config"] = json.loads(item["objects_config"])
                    except Exception:
                        item["objects_config"] = []
                else:
                    item["objects_config"] = []
                result.append(item)
            return result

    def get_plan(self, plan_id: int) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM migration_plans WHERE id = ?", (plan_id,))
            row = cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            if item.get("objects_config"):
                try:
                    item["objects_config"] = json.loads(item["objects_config"])
                except Exception:
                    item["objects_config"] = []
            else:
                item["objects_config"] = []
            return item

    def save_plan(self, data: Dict[str, Any]) -> int:
        """保存或更新迁移方案"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        objects_json = json.dumps(data.get("objects_config", []), ensure_ascii=False)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            plan_id = data.get("id")
            
            if plan_id:
                cursor.execute("""
                    UPDATE migration_plans
                    SET name = ?, description = ?, source_ds_id = ?, target_ds_id = ?,
                        source_db = ?, target_db = ?, strategy = ?, objects_config = ?, updated_at = ?
                    WHERE id = ?
                """, (
                    data["name"], data.get("description", ""),
                    data.get("source_ds_id"), data.get("target_ds_id"),
                    data["source_db"], data["target_db"],
                    data.get("strategy", "diff"), objects_json, now, plan_id
                ))
                conn.commit()
                return plan_id
            else:
                # 检查同名方案，如果同名则覆盖或报错，这里做 upsert 支持
                cursor.execute("SELECT id FROM migration_plans WHERE name = ?", (data["name"],))
                existing = cursor.fetchone()
                if existing:
                    existing_id = existing["id"]
                    cursor.execute("""
                        UPDATE migration_plans
                        SET description = ?, source_ds_id = ?, target_ds_id = ?,
                            source_db = ?, target_db = ?, strategy = ?, objects_config = ?, updated_at = ?
                        WHERE id = ?
                    """, (
                        data.get("description", ""),
                        data.get("source_ds_id"), data.get("target_ds_id"),
                        data["source_db"], data["target_db"],
                        data.get("strategy", "diff"), objects_json, now, existing_id
                    ))
                    conn.commit()
                    return existing_id
                else:
                    cursor.execute("""
                        INSERT INTO migration_plans (
                            name, description, source_ds_id, target_ds_id,
                            source_db, target_db, strategy, objects_config, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        data["name"], data.get("description", ""),
                        data.get("source_ds_id"), data.get("target_ds_id"),
                        data["source_db"], data["target_db"],
                        data.get("strategy", "diff"), objects_json, now, now
                    ))
                    conn.commit()
                    return cursor.lastrowid

    def delete_plan(self, plan_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM migration_plans WHERE id = ?", (plan_id,))
            conn.commit()
            return cursor.rowcount > 0

    def rename_plan(self, plan_id: int, new_name: str) -> bool:
        """重命名迁移方案"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 检查是否有除自身以外的同名方案
            cursor.execute("SELECT id FROM migration_plans WHERE name = ? AND id != ?", (new_name, plan_id))
            if cursor.fetchone():
                raise ValueError(f"已存在名为【{new_name}】的方案，名称不可重复！")
            cursor.execute("""
                UPDATE migration_plans
                SET name = ?, updated_at = ?
                WHERE id = ?
            """, (new_name, now, plan_id))
            conn.commit()
            return cursor.rowcount > 0

    # ==================== 导出方案 CRUD ====================

    def get_export_plans(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM export_plans ORDER BY updated_at DESC")
            rows = cursor.fetchall()
            result = []
            for row in rows:
                item = dict(row)
                if item.get("objects_config"):
                    try:
                        item["objects_config"] = json.loads(item["objects_config"])
                    except Exception:
                        item["objects_config"] = []
                else:
                    item["objects_config"] = []
                result.append(item)
            return result

    def get_export_plan(self, plan_id: int) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM export_plans WHERE id = ?", (plan_id,))
            row = cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            if item.get("objects_config"):
                try:
                    item["objects_config"] = json.loads(item["objects_config"])
                except Exception:
                    item["objects_config"] = []
            else:
                item["objects_config"] = []
            return item

    def save_export_plan(self, data: Dict[str, Any]) -> int:
        """保存或更新导出方案"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        objects_json = json.dumps(data.get("objects_config", []), ensure_ascii=False)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            plan_id = data.get("id")
            
            if plan_id:
                cursor.execute("""
                    UPDATE export_plans
                    SET name = ?, description = ?, source_ds_id = ?,
                        source_db = ?, export_mode = ?, objects_config = ?, updated_at = ?
                    WHERE id = ?
                """, (
                    data["name"], data.get("description", ""),
                    data.get("source_ds_id"),
                    data["source_db"], data.get("export_mode", "auto"),
                    objects_json, now, plan_id
                ))
                conn.commit()
                return plan_id
            else:
                # 检查同名方案，同名则覆盖更新，否则插入
                cursor.execute("SELECT id FROM export_plans WHERE name = ?", (data["name"],))
                existing = cursor.fetchone()
                if existing:
                    existing_id = existing["id"]
                    cursor.execute("""
                        UPDATE export_plans
                        SET description = ?, source_ds_id = ?,
                            source_db = ?, export_mode = ?, objects_config = ?, updated_at = ?
                        WHERE id = ?
                    """, (
                        data.get("description", ""),
                        data.get("source_ds_id"),
                        data["source_db"], data.get("export_mode", "auto"),
                        objects_json, now, existing_id
                    ))
                    conn.commit()
                    return existing_id
                else:
                    cursor.execute("""
                        INSERT INTO export_plans (
                            name, description, source_ds_id,
                            source_db, export_mode, objects_config, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        data["name"], data.get("description", ""),
                        data.get("source_ds_id"),
                        data["source_db"], data.get("export_mode", "auto"),
                        objects_json, now, now
                    ))
                    conn.commit()
                    return cursor.lastrowid

    def delete_export_plan(self, plan_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM export_plans WHERE id = ?", (plan_id,))
            conn.commit()
            return cursor.rowcount > 0

    def rename_export_plan(self, plan_id: int, new_name: str) -> bool:
        """重命名导出方案"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM export_plans WHERE name = ? AND id != ?", (new_name, plan_id))
            if cursor.fetchone():
                raise ValueError(f"已存在名为【{new_name}】的导出方案，名称不可重复！")
            cursor.execute("""
                UPDATE export_plans
                SET name = ?, updated_at = ?
                WHERE id = ?
            """, (new_name, now, plan_id))
            conn.commit()
            return cursor.rowcount > 0

