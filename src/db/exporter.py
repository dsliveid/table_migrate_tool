import os
import time
import shutil
import tempfile
import zipfile
import traceback
from datetime import datetime, date, time as dt_time
from decimal import Decimal
from uuid import UUID
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field

from .connection import MSSQLConnection
from .metadata import DatabaseMetadataExtractor, TableMetadata, ViewMetadata
from .generator import DDLGenerator


@dataclass
class ExportTaskConfig:
    source_ds: Dict[str, Any]
    source_db: str
    objects: List[Dict[str, Any]]
    output_path: str
    export_mode: str = "single_sql"  # "single_sql" or "zip"
    batch_size: int = 1000
    include_drop: bool = True
    include_skip: bool = False
    include_comments: bool = True
    include_indexes: bool = True


class ExportProgressSignal:
    """导出进度与日志信号回调"""

    def __init__(self):
        self.log_callback: Optional[Callable[[str, str], None]] = None
        self.progress_callback: Optional[Callable[[int, int, str], None]] = None
        self.finished_callback: Optional[Callable[[bool, Dict[str, Any]], None]] = None

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


def format_sql_literal(val: Any) -> str:
    """将 Python 对象安全转换为符合 T-SQL 规范的字面量表示"""
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, str):
        escaped = val.replace("'", "''")
        return f"N'{escaped}'"
    if isinstance(val, (bytes, bytearray)):
        return "0x" + val.hex()
    if isinstance(val, datetime):
        # 兼容毫秒显示
        return f"'{val.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}'"
    if isinstance(val, date):
        return f"'{val.strftime('%Y-%m-%d')}'"
    if isinstance(val, dt_time):
        return f"'{val.strftime('%H:%M:%S.%f')[:-3]}'"
    if isinstance(val, UUID):
        return f"'{val}'"
    escaped = str(val).replace("'", "''")
    return f"N'{escaped}'"


class ExportEngine:
    """数据库对象与数据导出引擎"""

    def __init__(self, config: ExportTaskConfig, signals: Optional[ExportProgressSignal] = None):
        self.config = config
        self.signals = signals or ExportProgressSignal()
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True
        self.signals.log("WARN", "收到取消请求，正在安全停止导出任务...")

    def execute(self) -> Dict[str, Any]:
        start_time = time.time()
        summary = {
            "success": True,
            "total_objects": len(self.config.objects),
            "tables_processed": 0,
            "views_processed": 0,
            "success_count": 0,
            "failed_count": 0,
            "total_rows_exported": 0,
            "errors": [],
            "elapsed_seconds": 0,
            "output_file": self.config.output_path,
            "file_size_bytes": 0
        }

        self.signals.log("INFO", "=== 导出任务启动 ===")
        self.signals.log("INFO", f"数据源: {self.config.source_ds.get('name')} [{self.config.source_db}]")
        self.signals.log("INFO", f"导出模式: {'单 SQL 文件' if self.config.export_mode == 'single_sql' else '多表独立脚本打包 ZIP'}")
        self.signals.log("INFO", f"目标输出路径: {self.config.output_path}")
        self.signals.log("INFO", f"待导出对象: {len(self.config.objects)} 项")

        # 确保输出目录存在
        out_dir = os.path.dirname(os.path.abspath(self.config.output_path))
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        source_conn = MSSQLConnection(self.config.source_ds, database=self.config.source_db)

        try:
            source_conn.connect()
            src_extractor = DatabaseMetadataExtractor(source_conn)

            tables = [obj for obj in self.config.objects if obj.get("type", "TABLE") == "TABLE"]
            views = [obj for obj in self.config.objects if obj.get("type") == "VIEW"]
            total_items = len(tables) + len(views)

            if self.config.export_mode == "zip":
                self._execute_zip_export(src_extractor, source_conn, tables, views, total_items, summary)
            else:
                self._execute_single_sql_export(src_extractor, source_conn, tables, views, total_items, summary)

            if os.path.exists(self.config.output_path):
                summary["file_size_bytes"] = os.path.getsize(self.config.output_path)

        except Exception as e:
            summary["success"] = False
            err_msg = f"导出过程出现严重异常: {e}"
            summary["errors"].append(err_msg)
            self.signals.log("ERROR", err_msg)
            self.signals.log("DEBUG", traceback.format_exc())
        finally:
            try:
                source_conn.close()
            except Exception:
                pass

        elapsed = round(time.time() - start_time, 2)
        summary["elapsed_seconds"] = elapsed

        if self.is_cancelled:
            summary["success"] = False
            self.signals.log("WARN", f"导出任务已被取消，耗时 {elapsed} 秒。")
        elif summary["success"] and summary["failed_count"] == 0:
            size_mb = summary["file_size_bytes"] / (1024 * 1024)
            self.signals.log("INFO", f"=== 导出圆满完成！耗时: {elapsed} 秒, 生成大小: {size_mb:.2f} MB ===")
        else:
            self.signals.log("WARN", f"=== 导出完成但存在失败项 (失败 {summary['failed_count']} 项) ===")

        self.signals.finish(summary["success"], summary)
        return summary

    # ---------------- 单 SQL 文件导出 ----------------
    def _execute_single_sql_export(
        self,
        extractor: DatabaseMetadataExtractor,
        conn: MSSQLConnection,
        tables: List[Dict[str, Any]],
        views: List[Dict[str, Any]],
        total_items: int,
        summary: Dict[str, Any]
    ):
        target_path = self.config.output_path
        current_item = 0

        with open(target_path, "w", encoding="utf-8-sig") as f:
            # 写入全局头部注释
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"-- ========================================================\n")
            f.write(f"-- SQL Server 表结构与数据导出脚本\n")
            f.write(f"-- 来源数据库: [{self.config.source_db}]\n")
            f.write(f"-- 导出生成时间: {now_str}\n")
            f.write(f"-- 表总数: {len(tables)} 张, 视图总数: {len(views)} 个\n")
            f.write(f"-- ========================================================\n\n")
            f.write(f"USE [{self.config.source_db}];\n")
            f.write(f"GO\n\n")

            # 1. 导出表结构及数据
            for item in tables:
                if self.is_cancelled:
                    break
                current_item += 1
                schema = item.get("schema", "dbo")
                name = item["name"]
                export_data = bool(item.get("export_data") or item.get("migrate_data", False))
                self.signals.progress(current_item, total_items, f"正在导出表 [{schema}].[{name}]...")

                try:
                    rows = self._export_table_to_stream(extractor, conn, schema, name, export_data, f)
                    summary["tables_processed"] += 1
                    summary["success_count"] += 1
                    summary["total_rows_exported"] += rows
                except Exception as e:
                    summary["tables_processed"] += 1
                    summary["failed_count"] += 1
                    err_msg = f"表 [{schema}].[{name}] 导出失败: {e}"
                    summary["errors"].append(err_msg)
                    self.signals.log("ERROR", err_msg)
                    self.signals.log("DEBUG", traceback.format_exc())

            # 2. 导出视图结构
            if views and not self.is_cancelled:
                f.write("\n-- ========================================================\n")
                f.write("-- 视图定义部分\n")
                f.write("-- ========================================================\n\n")

                for v_item in views:
                    if self.is_cancelled:
                        break
                    current_item += 1
                    v_schema = v_item.get("schema", "dbo")
                    v_name = v_item["name"]
                    custom_sql = v_item.get("custom_sql")
                    self.signals.progress(current_item, total_items, f"正在导出视图 [{v_schema}].[{v_name}]...")

                    try:
                        self._export_view_to_stream(extractor, v_schema, v_name, custom_sql, f)
                        summary["views_processed"] += 1
                        summary["success_count"] += 1
                    except Exception as e:
                        summary["views_processed"] += 1
                        summary["failed_count"] += 1
                        err_msg = f"视图 [{v_schema}].[{v_name}] 导出失败: {e}"
                        summary["errors"].append(err_msg)
                        self.signals.log("ERROR", err_msg)
                        self.signals.log("DEBUG", traceback.format_exc())

    # ---------------- 多表分文件并打包 ZIP 压缩包 ----------------
    def _execute_zip_export(
        self,
        extractor: DatabaseMetadataExtractor,
        conn: MSSQLConnection,
        tables: List[Dict[str, Any]],
        views: List[Dict[str, Any]],
        total_items: int,
        summary: Dict[str, Any]
    ):
        current_item = 0
        temp_dir = tempfile.mkdtemp(prefix="export_mssql_")
        manifest_lines = [
            f"SQL Server 导出清单摘要",
            f"来源数据库: [{self.config.source_db}]",
            f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"导出对象总数: {total_items}",
            "",
            "--- 表清单 ---"
        ]

        try:
            # 1. 为每个表生成独立的 SQL 脚本
            for item in tables:
                if self.is_cancelled:
                    break
                current_item += 1
                schema = item.get("schema", "dbo")
                name = item["name"]
                export_data = bool(item.get("export_data") or item.get("migrate_data", False))
                self.signals.progress(current_item, total_items, f"正在生成表脚本 [{schema}].[{name}]...")

                table_filename = f"{schema}.{name}.sql"
                table_filepath = os.path.join(temp_dir, table_filename)

                try:
                    with open(table_filepath, "w", encoding="utf-8-sig") as f:
                        f.write(f"-- 表脚本: [{schema}].[{name}]\n")
                        f.write(f"-- 包含数据: {'是' if export_data else '否'}\n")
                        f.write(f"USE [{self.config.source_db}];\nGO\n\n")
                        rows = self._export_table_to_stream(extractor, conn, schema, name, export_data, f)
                    
                    summary["tables_processed"] += 1
                    summary["success_count"] += 1
                    summary["total_rows_exported"] += rows
                    manifest_lines.append(f"- [{schema}].[{name}]: {table_filename} ({'含数据 ' + str(rows) + ' 行' if export_data else '仅结构'})")
                except Exception as e:
                    summary["tables_processed"] += 1
                    summary["failed_count"] += 1
                    err_msg = f"表 [{schema}].[{name}] 生成失败: {e}"
                    summary["errors"].append(err_msg)
                    self.signals.log("ERROR", err_msg)
                    self.signals.log("DEBUG", traceback.format_exc())

            # 2. 生成视图脚本
            if views and not self.is_cancelled:
                manifest_lines.append("")
                manifest_lines.append("--- 视图清单 ---")
                views_filename = "views.sql"
                views_filepath = os.path.join(temp_dir, views_filename)

                with open(views_filepath, "w", encoding="utf-8-sig") as f:
                    f.write(f"-- 视图汇总脚本 (共 {len(views)} 个)\n")
                    f.write(f"USE [{self.config.source_db}];\nGO\n\n")

                    for v_item in views:
                        if self.is_cancelled:
                            break
                        current_item += 1
                        v_schema = v_item.get("schema", "dbo")
                        v_name = v_item["name"]
                        custom_sql = v_item.get("custom_sql")
                        self.signals.progress(current_item, total_items, f"正在生成视图脚本 [{v_schema}].[{v_name}]...")

                        try:
                            self._export_view_to_stream(extractor, v_schema, v_name, custom_sql, f)
                            summary["views_processed"] += 1
                            summary["success_count"] += 1
                            manifest_lines.append(f"- 视图 [{v_schema}].[{v_name}] ({'已自定义' if custom_sql else '源库原始'})")
                        except Exception as e:
                            summary["views_processed"] += 1
                            summary["failed_count"] += 1
                            err_msg = f"视图 [{v_schema}].[{v_name}] 导出失败: {e}"
                            summary["errors"].append(err_msg)
                            self.signals.log("ERROR", err_msg)
                            self.signals.log("DEBUG", traceback.format_exc())

            # 3. 写入说明清单文件
            manifest_path = os.path.join(temp_dir, "_README.txt")
            with open(manifest_path, "w", encoding="utf-8") as f:
                f.write("\n".join(manifest_lines) + "\n")

            # 4. 打包为 ZIP 压缩包
            if not self.is_cancelled:
                self.signals.log("INFO", f"正在将所有脚本合并打包为 ZIP 压缩包: {self.config.output_path}...")
                with zipfile.ZipFile(self.config.output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    for root, _, files in os.walk(temp_dir):
                        for file in files:
                            full_f = os.path.join(root, file)
                            rel_f = os.path.relpath(full_f, temp_dir)
                            zf.write(full_f, arcname=rel_f)
                self.signals.log("INFO", f"ZIP 压缩包打包完成！共封装 {len(tables) + (1 if views else 0) + 1} 个文件。")

        finally:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

    # ---------------- 单表导出核心流处理 ----------------
    def _export_table_to_stream(
        self,
        extractor: DatabaseMetadataExtractor,
        conn: MSSQLConnection,
        schema: str,
        name: str,
        export_data: bool,
        f
    ) -> int:
        table = extractor.extract_table(schema, name)

        f.write(f"-- --------------------------------------------------------\n")
        f.write(f"-- 表: {table.full_name}\n")
        f.write(f"-- --------------------------------------------------------\n")

        # 1. DROP 语句（若配置启用）
        if self.config.include_drop:
            f.write(f"IF OBJECT_ID(N'{table.full_name}', 'U') IS NOT NULL\n")
            f.write(f"    DROP TABLE {table.full_name};\n")
            f.write(f"GO\n\n")

        # 2. CREATE TABLE 语句
        create_stmts = DDLGenerator.generate_create_table(table)
        create_table_sql = create_stmts[0].strip() if create_stmts else ""

        if self.config.include_skip:
            f.write(f"IF OBJECT_ID(N'{table.full_name}', 'U') IS NULL\n")
            f.write("BEGIN\n")
            f.write(f"{create_table_sql}\n")
            f.write("END\nGO\n\n")
        else:
            if create_table_sql:
                f.write(f"{create_table_sql}\nGO\n\n")

        # 3. 辅助索引生成 (排除主键)
        if self.config.include_indexes:
            for idx in table.indexes:
                if idx.is_primary_key or not idx.columns:
                    continue
                unique_str = "UNIQUE " if idx.is_unique else ""
                type_str = idx.type_desc or "NONCLUSTERED"
                key_cols = [f"[{c[0]}] {'DESC' if c[1] else 'ASC'}" for c in idx.columns]
                inc_str = f" INCLUDE ({', '.join([f'[{c}]' for c in idx.included_columns])})" if idx.included_columns else ""
                idx_sql = f"CREATE {unique_str}{type_str} INDEX [{idx.name}] ON {table.full_name} ({', '.join(key_cols)}){inc_str};"

                if self.config.include_skip:
                    f.write(f"IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'{table.full_name}') AND name = N'{idx.name}')\n")
                    f.write(f"    {idx_sql}\nGO\n\n")
                else:
                    f.write(f"{idx_sql}\nGO\n\n")

        # 4. 表与列注释
        if self.config.include_comments:
            if table.description:
                desc_val = table.description.replace("'", "''")
                table_comment = (
                    f"EXEC sys.sp_addextendedproperty "
                    f"@name = N'MS_Description', @value = N'{desc_val}', "
                    f"@level0type = N'SCHEMA', @level0name = N'{table.schema}', "
                    f"@level1type = N'TABLE', @level1name = N'{table.name}';"
                )
                if self.config.include_skip:
                    f.write(f"IF NOT EXISTS (SELECT 1 FROM sys.fn_listextendedproperty(N'MS_Description', N'SCHEMA', N'{table.schema}', N'TABLE', N'{table.name}', NULL, NULL))\n")
                    f.write(f"    {table_comment}\nGO\n\n")
                else:
                    f.write(f"{table_comment}\nGO\n\n")

            for col in table.columns:
                if col.description:
                    col_desc = col.description.replace("'", "''")
                    col_comment = (
                        f"EXEC sys.sp_addextendedproperty "
                        f"@name = N'MS_Description', @value = N'{col_desc}', "
                        f"@level0type = N'SCHEMA', @level0name = N'{table.schema}', "
                        f"@level1type = N'TABLE', @level1name = N'{table.name}', "
                        f"@level2type = N'COLUMN', @level2name = N'{col.name}';"
                    )
                    if self.config.include_skip:
                        f.write(f"IF NOT EXISTS (SELECT 1 FROM sys.fn_listextendedproperty(N'MS_Description', N'SCHEMA', N'{table.schema}', N'TABLE', N'{table.name}', N'COLUMN', N'{col.name}'))\n")
                        f.write(f"    {col_comment}\nGO\n\n")
                    else:
                        f.write(f"{col_comment}\nGO\n\n")

        # 5. 数据导出
        total_rows = 0
        if export_data:
            col_names = [col.name for col in table.columns]
            if col_names:
                has_identity = any(col.is_identity for col in table.columns)
                cols_bracketed = ", ".join([f"[{c}]" for c in col_names])

                if has_identity:
                    f.write(f"SET IDENTITY_INSERT {table.full_name} ON;\nGO\n\n")

                select_sql = f"SELECT {cols_bracketed} FROM {table.full_name}"
                cursor = conn._conn.cursor()
                try:
                    cursor.execute(select_sql)
                    while True:
                        if self.is_cancelled:
                            break
                        rows = cursor.fetchmany(self.config.batch_size)
                        if not rows:
                            break

                        val_lines = []
                        for r in rows:
                            if isinstance(r, dict):
                                row_vals = [format_sql_literal(r[c]) for c in col_names]
                            else:
                                row_vals = [format_sql_literal(v) for v in r]
                            val_lines.append(f"({', '.join(row_vals)})")

                        if val_lines:
                            # SQL Server 最多支持每个 INSERT 语句插入 1000 行
                            chunk_size = 1000
                            for i in range(0, len(val_lines), chunk_size):
                                chunk = val_lines[i:i + chunk_size]
                                f.write(f"INSERT INTO {table.full_name} ({cols_bracketed}) VALUES\n")
                                f.write(",\n".join(chunk) + ";\nGO\n\n")
                            total_rows += len(val_lines)
                finally:
                    cursor.close()

                if has_identity:
                    f.write(f"SET IDENTITY_INSERT {table.full_name} OFF;\nGO\n\n")

        self.signals.log("INFO", f"表 {table.full_name} 导出完成 ({'结构+数据 ' + str(total_rows) + ' 行' if export_data else '仅结构'})。")
        return total_rows

    # ---------------- 单视图导出核心流处理 ----------------
    def _export_view_to_stream(
        self,
        extractor: DatabaseMetadataExtractor,
        schema: str,
        name: str,
        custom_sql: Optional[str],
        f
    ):
        f.write(f"-- --------------------------------------------------------\n")
        f.write(f"-- 视图: [{schema}].[{name}]\n")
        f.write(f"-- --------------------------------------------------------\n")

        if custom_sql and custom_sql.strip():
            view_definition = custom_sql.strip()
            desc = None
            try:
                view = extractor.extract_view(schema, name)
                desc = view.description
            except Exception:
                pass
        else:
            view = extractor.extract_view(schema, name)
            view_definition = (view.definition or "").strip()
            desc = view.description

        # 1. DROP 语句（若配置启用）
        if self.config.include_drop:
            f.write(f"IF OBJECT_ID(N'[{schema}].[{name}]', 'V') IS NOT NULL\n")
            f.write(f"    DROP VIEW [{schema}].[{name}];\n")
            f.write(f"GO\n\n")

        # 2. CREATE VIEW 语句
        if self.config.include_skip:
            escaped_sql = view_definition.replace("'", "''")
            f.write(f"IF OBJECT_ID(N'[{schema}].[{name}]', 'V') IS NULL\n")
            f.write(f"BEGIN\n")
            f.write(f"    EXEC sp_executesql N'{escaped_sql}';\n")
            f.write(f"END\nGO\n\n")
        else:
            batches = DDLGenerator._split_sql_batches(view_definition)
            for b in batches:
                f.write(b.strip() + "\nGO\n\n")

        # 3. 视图注释
        if self.config.include_comments and desc:
            desc_val = desc.replace("'", "''")
            comment_sql = (
                f"EXEC sys.sp_addextendedproperty "
                f"@name = N'MS_Description', @value = N'{desc_val}', "
                f"@level0type = N'SCHEMA', @level0name = N'{schema}', "
                f"@level1type = N'VIEW', @level1name = N'{name}';"
            )
            if self.config.include_skip:
                f.write(f"IF NOT EXISTS (SELECT 1 FROM sys.fn_listextendedproperty(N'MS_Description', N'SCHEMA', N'{schema}', N'VIEW', N'{name}', NULL, NULL))\n")
                f.write(f"    {comment_sql}\nGO\n\n")
            else:
                f.write(f"{comment_sql}\nGO\n\n")

        self.signals.log("INFO", f"视图 [{schema}].[{name}] 导出完成 ({'已使用自定义SQL' if custom_sql else '源库原始定义'})。")
