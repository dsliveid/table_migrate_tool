import time
import traceback
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass
from .connection import MSSQLConnection
from .metadata import DatabaseMetadataExtractor, TableMetadata, ViewMetadata
from .generator import DDLGenerator


@dataclass
class MigrationTaskConfig:
    source_ds: Dict[str, Any]
    target_ds: Dict[str, Any]
    source_db: str
    target_db: str
    strategy: str  # 'diff', 'recreate', 'skip'
    objects: List[Dict[str, Any]]  # [{"schema": "dbo", "name": "Users", "type": "TABLE", "migrate_data": True}]


class MigrationProgressSignal:
    """迁移事件与进度回调接口，供 GUI 线程订阅"""
    def __init__(self):
        self.log_callback: Optional[Callable[[str, str], None]] = None  # level, msg
        self.progress_callback: Optional[Callable[[int, int, str], None]] = None  # current, total, text
        self.finished_callback: Optional[Callable[[bool, Dict[str, Any]], None]] = None  # success, summary

    def log(self, level: str, msg: str):
        if self.log_callback:
            self.log_callback(level, msg)
        else:
            print(f"[{level}] {msg}")

    def progress(self, current: int, total: int, text: str):
        if self.progress_callback:
            self.progress_callback(current, total, text)

    def finish(self, success: bool, summary: Dict[str, Any]):
        if self.finished_callback:
            self.finished_callback(success, summary)


class MigrationEngine:
    """核心数据库迁移执行引擎"""

    def __init__(self, config: MigrationTaskConfig, signals: Optional[MigrationProgressSignal] = None):
        self.config = config
        self.signals = signals or MigrationProgressSignal()
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True
        self.signals.log("WARN", "收到取消信号，正在准备安全停止...")

    def execute(self) -> Dict[str, Any]:
        start_time = time.time()
        summary = {
            "success": True,
            "total_objects": len(self.config.objects),
            "tables_processed": 0,
            "views_processed": 0,
            "success_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "total_rows_copied": 0,
            "errors": [],
            "elapsed_seconds": 0
        }

        self.signals.log("INFO", f"=== 迁移任务启动 ===")
        self.signals.log("INFO", f"来源: {self.config.source_ds.get('name')} [{self.config.source_db}]")
        self.signals.log("INFO", f"目标: {self.config.target_ds.get('name')} [{self.config.target_db}]")
        self.signals.log("INFO", f"冲突处理策略: {self.config.strategy}")
        self.signals.log("INFO", f"待迁移对象总计: {len(self.config.objects)} 项")

        source_conn = MSSQLConnection(self.config.source_ds, database=self.config.source_db)
        target_conn = MSSQLConnection(self.config.target_ds, database=self.config.target_db)

        try:
            source_conn.connect()
            # 确保目标数据库存在，若不存在则尝试在 master 下创建
            self._ensure_target_database_exists()
            target_conn.connect()

            src_extractor = DatabaseMetadataExtractor(source_conn)
            tgt_extractor = DatabaseMetadataExtractor(target_conn)

            # 分离表和视图（表优先迁移，视图最后迁移）
            tables = [obj for obj in self.config.objects if obj.get("type", "TABLE") == "TABLE"]
            views = [obj for obj in self.config.objects if obj.get("type") == "VIEW"]

            total_items = len(tables) + len(views)
            current_item = 0

            # 1. 执行表迁移
            for item in tables:
                if self.is_cancelled:
                    self.signals.log("WARN", "迁移任务已由用户取消。")
                    summary["success"] = False
                    break

                current_item += 1
                schema = item.get("schema", "dbo")
                name = item["name"]
                migrate_data = bool(item.get("migrate_data", False))
                self.signals.progress(current_item, total_items, f"正在迁移表 [{schema}].[{name}]...")

                try:
                    rows_copied = self._migrate_single_table(
                        src_extractor, tgt_extractor, source_conn, target_conn,
                        schema, name, migrate_data
                    )
                    summary["tables_processed"] += 1
                    summary["success_count"] += 1
                    summary["total_rows_copied"] += rows_copied
                except Exception as e:
                    summary["tables_processed"] += 1
                    summary["failed_count"] += 1
                    err_msg = f"表 [{schema}].[{name}] 迁移失败: {e}"
                    summary["errors"].append(err_msg)
                    self.signals.log("ERROR", err_msg)
                    self.signals.log("DEBUG", traceback.format_exc())

            # 2. 执行视图迁移（多轮重试机制，解决视图间相互依赖问题）
            if views and not self.is_cancelled:
                self.signals.log("INFO", f"开始迁移视图（共 {len(views)} 个，强制仅结构迁移）...")
                failed_views = list(views)
                max_retries = 3

                for retry in range(max_retries):
                    if not failed_views or self.is_cancelled:
                        break
                    
                    self.signals.log("INFO", f"执行视图迁移轮次 {retry + 1}/{max_retries}，待处理 {len(failed_views)} 个视图...")
                    still_failed = []
                    
                    for v_item in failed_views:
                        if self.is_cancelled:
                            break
                        v_schema = v_item.get("schema", "dbo")
                        v_name = v_item["name"]
                        v_custom_sql = v_item.get("custom_sql")
                        try:
                            self._migrate_single_view(src_extractor, target_conn, v_schema, v_name, v_custom_sql)
                            summary["views_processed"] += 1
                            summary["success_count"] += 1
                        except Exception as e:
                            if retry == max_retries - 1:
                                summary["views_processed"] += 1
                                summary["failed_count"] += 1
                                tag = "(使用自定义SQL) " if (v_custom_sql and v_custom_sql.strip()) else ""
                                err_msg = f"视图 [{v_schema}].[{v_name}] {tag}迁移失败: {e}"
                                summary["errors"].append(err_msg)
                                self.signals.log("ERROR", err_msg)
                            else:
                                still_failed.append(v_item)
                                
                    failed_views = still_failed

        except Exception as e:
            summary["success"] = False
            summary["errors"].append(f"全局执行异常: {str(e)}")
            self.signals.log("ERROR", f"全局执行异常: {str(e)}")
            self.signals.log("DEBUG", traceback.format_exc())
        finally:
            source_conn.close()
            target_conn.close()

        summary["elapsed_seconds"] = round(time.time() - start_time, 2)
        self.signals.log("INFO", f"=== 迁移完成 ===")
        self.signals.log("INFO", f"成功: {summary['success_count']}，失败: {summary['failed_count']}，总行数: {summary['total_rows_copied']}，耗时: {summary['elapsed_seconds']} 秒")
        self.signals.finish(summary["success"], summary)
        return summary

    def _ensure_target_database_exists(self):
        """检查目标库是否存在，不存在则创建"""
        master_conn = MSSQLConnection(self.config.target_ds, database="master")
        try:
            with master_conn:
                chk_sql = "SELECT database_id FROM sys.databases WHERE name = %s"
                rows = master_conn.query(chk_sql, (self.config.target_db,))
                if not rows:
                    self.signals.log("WARN", f"目标端不存在数据库 [{self.config.target_db}]，正在自动创建...")
                    master_conn.execute(f"CREATE DATABASE [{self.config.target_db}];")
                    self.signals.log("INFO", f"目标端数据库 [{self.config.target_db}] 创建成功。")
        except Exception as e:
            self.signals.log("WARN", f"检查/创建目标数据库时提示: {e}")

    def _table_exists(self, conn: MSSQLConnection, schema: str, name: str) -> bool:
        sql = "SELECT 1 AS [exists_flag] FROM sys.tables t INNER JOIN sys.schemas s ON t.schema_id = s.schema_id WHERE s.name = %s AND t.name = %s"
        rows = conn.query(sql, (schema, name))
        return len(rows) > 0

    def _migrate_single_table(
        self,
        src_extractor: DatabaseMetadataExtractor,
        tgt_extractor: DatabaseMetadataExtractor,
        src_conn: MSSQLConnection,
        tgt_conn: MSSQLConnection,
        schema: str,
        name: str,
        migrate_data: bool
    ) -> int:
        """迁移单表结构与数据，返回迁移的数据行数"""
        src_table = src_extractor.extract_table(schema, name)
        exists_on_target = self._table_exists(tgt_conn, schema, name)
        table_recreated = False

        if exists_on_target:
            if self.config.strategy == "skip":
                self.signals.log("INFO", f"表 [{schema}].[{name}] 在目标库已存在，跳过结构更新。")
            elif self.config.strategy == "recreate":
                self.signals.log("WARN", f"表 [{schema}].[{name}] 已存在，执行【先删后建兜底】...")
                fks = tgt_extractor.get_table_foreign_keys_referencing(schema, name)
                drop_stmts = DDLGenerator.generate_drop_table(schema, name, fks)
                for sql in drop_stmts:
                    tgt_conn.execute(sql)
                
                # 全新创建
                create_stmts = DDLGenerator.generate_create_table(src_table)
                for sql in create_stmts:
                    tgt_conn.execute(sql)
                table_recreated = True
                self.signals.log("INFO", f"表 [{schema}].[{name}] 先删后建完成。")
            elif self.config.strategy == "diff":
                tgt_table = tgt_extractor.extract_table(schema, name)
                
                # 检查主键是否被调整
                if DDLGenerator.is_pk_changed(src_table, tgt_table):
                    self.signals.log("WARN", f"表 [{schema}].[{name}] 检测到主键变动，自动触发【先删后建兜底方案】...")
                    fks = tgt_extractor.get_table_foreign_keys_referencing(schema, name)
                    drop_stmts = DDLGenerator.generate_drop_table(schema, name, fks)
                    for sql in drop_stmts:
                        tgt_conn.execute(sql)
                    create_stmts = DDLGenerator.generate_create_table(src_table)
                    for sql in create_stmts:
                        tgt_conn.execute(sql)
                    table_recreated = True
                    self.signals.log("INFO", f"表 [{schema}].[{name}] 重构完成。")
                else:
                    # 增量差异 ALTER
                    diff_stmts = DDLGenerator.generate_diff_statements(src_table, tgt_table)
                    if diff_stmts:
                        self.signals.log("INFO", f"表 [{schema}].[{name}] 发现结构差异，执行 {len(diff_stmts)} 条增量更新语句...")
                        for sql in diff_stmts:
                            tgt_conn.execute(sql)
                        self.signals.log("INFO", f"表 [{schema}].[{name}] 增量同步完成。")
                    else:
                        self.signals.log("INFO", f"表 [{schema}].[{name}] 结构无变动。")
        else:
            # 目标库不存在，直接全新建表
            self.signals.log("INFO", f"目标表 [{schema}].[{name}] 不存在，全新创建...")
            create_stmts = DDLGenerator.generate_create_table(src_table)
            for sql in create_stmts:
                tgt_conn.execute(sql)
            self.signals.log("INFO", f"表 [{schema}].[{name}] 创建成功。")

        # 数据迁移逻辑
        rows_copied = 0
        if migrate_data:
            rows_copied = self._copy_table_data(src_conn, tgt_conn, src_table, table_recreated)
            
        return rows_copied

    def _copy_table_data(
        self,
        src_conn: MSSQLConnection,
        tgt_conn: MSSQLConnection,
        table: TableMetadata,
        table_was_recreated: bool
    ) -> int:
        """高效批量迁移单表数据"""
        col_names = [col.name for col in table.columns]
        if not col_names:
            return 0

        cols_str = ", ".join([f"[{c}]" for c in col_names])
        placeholders = ", ".join(["%s"] * len(col_names))
        insert_sql = f"INSERT INTO {table.full_name} ({cols_str}) VALUES ({placeholders})"

        has_identity = any(col.is_identity for col in table.columns)

        # 若未重建且目标表已有数据，可以先清空目标表以保证幂等，或使用 append
        if not table_was_recreated:
            # 清空目标表
            try:
                tgt_conn.execute(f"TRUNCATE TABLE {table.full_name};")
            except Exception:
                tgt_conn.execute(f"DELETE FROM {table.full_name};")

        # 开启 IDENTITY_INSERT
        if has_identity:
            tgt_conn.execute(f"SET IDENTITY_INSERT {table.full_name} ON;")

        total_rows = 0
        batch_size = 2000
        try:
            # 分批拉取并写入
            select_sql = f"SELECT {cols_str} FROM {table.full_name}"
            # 获取原始 cursor 流式读取
            src_cursor = src_conn._conn.cursor()
            tgt_cursor = tgt_conn._conn.cursor()
            try:
                src_cursor.execute(select_sql)
                while True:
                    if self.is_cancelled:
                        break
                    rows = src_cursor.fetchmany(batch_size)
                    if not rows:
                        break
                    
                    # 将 row 字典转换为按字段顺序的值元组
                    val_tuples = []
                    for r in rows:
                        if isinstance(r, dict):
                            val_tuples.append(tuple(r[c] for c in col_names))
                        else:
                            val_tuples.append(r)
                            
                    tgt_cursor.executemany(insert_sql, val_tuples)
                    tgt_conn._conn.commit()
                    total_rows += len(rows)
            finally:
                src_cursor.close()
                tgt_cursor.close()

            self.signals.log("INFO", f"表 {table.full_name} 数据迁移完成，共写入 {total_rows} 行。")
        finally:
            if has_identity:
                try:
                    tgt_conn.execute(f"SET IDENTITY_INSERT {table.full_name} OFF;")
                except Exception:
                    pass

        return total_rows

    def _migrate_single_view(
        self,
        src_extractor: DatabaseMetadataExtractor,
        tgt_conn: MSSQLConnection,
        schema: str,
        name: str,
        custom_sql: Optional[str] = None
    ):
        """迁移单视图（强制仅结构，支持自定义 SQL 覆盖）"""
        if custom_sql and custom_sql.strip():
            desc = None
            try:
                view = src_extractor.extract_view(schema, name)
                desc = view.description
            except Exception:
                pass
            stmts = DDLGenerator.generate_create_view_from_sql(schema, name, custom_sql.strip(), description=desc)
            self.signals.log("INFO", f"正在使用自定义 SQL 创建视图 [{schema}].[{name}]...")
            for sql in stmts:
                tgt_conn.execute(sql)
            self.signals.log("INFO", f"视图 [{schema}].[{name}] (使用自定义SQL) 迁移成功。")
        else:
            view = src_extractor.extract_view(schema, name)
            stmts = DDLGenerator.generate_create_view(view)
            for sql in stmts:
                tgt_conn.execute(sql)
            self.signals.log("INFO", f"视图 [{schema}].[{name}] 迁移成功。")

