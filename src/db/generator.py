from typing import List, Dict, Any, Optional
from .metadata import TableMetadata, ColumnMetadata, IndexMetadata, ViewMetadata


class DDLGenerator:
    """SQL Server DDL 生成与结构差异对比生成器"""

    @staticmethod
    def generate_create_table(table: TableMetadata) -> List[str]:
        """生成创建表的完整 SQL 语句列表（表结构、主键、索引、注释）"""
        statements = []

        # 1. 组装列与主键定义
        col_defs = []
        for col in table.columns:
            sql_type = col.get_sql_type()
            parts = [f"[{col.name}]", sql_type]
            
            if col.is_identity:
                parts.append(f"IDENTITY({col.identity_seed},{col.identity_increment})")
                
            if not col.is_nullable:
                parts.append("NOT NULL")
            else:
                parts.append("NULL")
                
            if col.default_definition:
                parts.append(f"DEFAULT {col.default_definition}")
                
            col_defs.append("    " + " ".join(parts))

        # 主键定义
        if table.primary_key and table.primary_key.columns:
            pk_cols = [f"[{c[0]}] {'DESC' if c[1] else 'ASC'}" for c in table.primary_key.columns]
            pk_type = table.primary_key.type_desc or "CLUSTERED"
            pk_str = f"    CONSTRAINT [{table.primary_key.name}] PRIMARY KEY {pk_type} ({', '.join(pk_cols)})"
            col_defs.append(pk_str)

        create_sql = f"CREATE TABLE {table.full_name} (\n" + ",\n".join(col_defs) + "\n);"
        statements.append(create_sql)

        # 2. 辅助索引生成 (排除主键)
        for idx in table.indexes:
            if idx.is_primary_key or not idx.columns:
                continue
            unique_str = "UNIQUE " if idx.is_unique else ""
            type_str = idx.type_desc or "NONCLUSTERED"
            key_cols = [f"[{c[0]}] {'DESC' if c[1] else 'ASC'}" for c in idx.columns]
            
            idx_sql = f"CREATE {unique_str}{type_str} INDEX [{idx.name}] ON {table.full_name} ({', '.join(key_cols)})"
            if idx.included_columns:
                inc_cols = [f"[{c}]" for c in idx.included_columns]
                idx_sql += f" INCLUDE ({', '.join(inc_cols)})"
            idx_sql += ";"
            statements.append(idx_sql)

        # 3. 表注释
        if table.description:
            desc_val = table.description.replace("'", "''")
            table_comment_sql = f"""
                EXEC sys.sp_addextendedproperty 
                    @name = N'MS_Description', 
                    @value = N'{desc_val}', 
                    @level0type = N'SCHEMA', @level0name = N'{table.schema}', 
                    @level1type = N'TABLE', @level1name = N'{table.name}';
            """
            statements.append(table_comment_sql.strip())

        # 4. 列注释
        for col in table.columns:
            if col.description:
                col_desc = col.description.replace("'", "''")
                col_comment_sql = f"""
                    EXEC sys.sp_addextendedproperty 
                        @name = N'MS_Description', 
                        @value = N'{col_desc}', 
                        @level0type = N'SCHEMA', @level0name = N'{table.schema}', 
                        @level1type = N'TABLE', @level1name = N'{table.name}', 
                        @level2type = N'COLUMN', @level2name = N'{col.name}';
                """
                statements.append(col_comment_sql.strip())

        return statements

    @staticmethod
    def generate_drop_table(schema_name: str, table_name: str, referencing_fks: Optional[List[Dict[str, Any]]] = None) -> List[str]:
        """生成安全删除表的 SQL 语句（先解依赖外键，再 DROP TABLE）"""
        statements = []
        if referencing_fks:
            for fk in referencing_fks:
                drop_fk = f"ALTER TABLE [{fk['parent_schema']}].[{fk['parent_table']}] DROP CONSTRAINT [{fk['fk_name']}];"
                statements.append(drop_fk)

        statements.append(f"IF OBJECT_ID('[{schema_name}].[{table_name}]', 'U') IS NOT NULL DROP TABLE [{schema_name}].[{table_name}];")
        return statements

    @staticmethod
    def generate_diff_statements(source_table: TableMetadata, target_table: TableMetadata) -> List[str]:
        """
        比对源表与目标表的结构差异，生成智能增量更新语句 (ALTER TABLE)
        返回执行语句列表。如果发现无法平滑升级（如主键不匹配），返回空列表或在调用方触发兜底。
        """
        statements = []
        target_cols_map = {c.name.lower(): c for c in target_table.columns}

        # 1. 检测源表有而目标表没有的字段 -> ADD COLUMN
        for col in source_table.columns:
            if col.name.lower() not in target_cols_map:
                sql_type = col.get_sql_type()
                parts = [f"ALTER TABLE {target_table.full_name} ADD [{col.name}] {sql_type}"]
                if not col.is_nullable:
                    # NOT NULL 字段需要默认值，否则如果是已有行会报错
                    if col.default_definition:
                        parts.append(f"NOT NULL DEFAULT {col.default_definition}")
                    else:
                        # 兼容处理：若无默认值，添加时先置为 NULL 避免报错
                        parts.append("NULL")
                else:
                    parts.append("NULL")
                    if col.default_definition:
                        parts.append(f"DEFAULT {col.default_definition}")
                
                add_col_sql = " ".join(parts) + ";"
                statements.append(add_col_sql)

                # 添加列注释
                if col.description:
                    desc_val = col.description.replace("'", "''")
                    col_comment_sql = f"""
                        EXEC sys.sp_addextendedproperty 
                            @name = N'MS_Description', 
                            @value = N'{desc_val}', 
                            @level0type = N'SCHEMA', @level0name = N'{target_table.schema}', 
                            @level1type = N'TABLE', @level1name = N'{target_table.name}', 
                            @level2type = N'COLUMN', @level2name = N'{col.name}';
                    """
                    statements.append(col_comment_sql.strip())
            else:
                # 2. 字段已存在，检测是否类型/长度/可空性发生变更 -> ALTER COLUMN
                t_col = target_cols_map[col.name.lower()]
                s_type = col.get_sql_type()
                t_type = t_col.get_sql_type()
                
                # 忽略大小写比对类型或可空性变动
                if s_type.lower() != t_type.lower() or col.is_nullable != t_col.is_nullable:
                    null_str = "NULL" if col.is_nullable else "NOT NULL"
                    alter_col_sql = f"ALTER TABLE {target_table.full_name} ALTER COLUMN [{col.name}] {s_type} {null_str};"
                    statements.append(alter_col_sql)

        # 3. 检测新索引
        target_idx_names = {idx.name.lower() for idx in target_table.indexes}
        for idx in source_table.indexes:
            if idx.is_primary_key or not idx.columns:
                continue
            if idx.name.lower() not in target_idx_names:
                unique_str = "UNIQUE " if idx.is_unique else ""
                type_str = idx.type_desc or "NONCLUSTERED"
                key_cols = [f"[{c[0]}] {'DESC' if c[1] else 'ASC'}" for c in idx.columns]
                idx_sql = f"CREATE {unique_str}{type_str} INDEX [{idx.name}] ON {target_table.full_name} ({', '.join(key_cols)})"
                if idx.included_columns:
                    inc_cols = [f"[{c}]" for c in idx.included_columns]
                    idx_sql += f" INCLUDE ({', '.join(inc_cols)})"
                idx_sql += ";"
                statements.append(idx_sql)

        return statements

    @staticmethod
    def is_pk_changed(source_table: TableMetadata, target_table: TableMetadata) -> bool:
        """检查主键是否发生变更（包括主键列顺序、主键列增删等）"""
        s_pk = source_table.primary_key
        t_pk = target_table.primary_key

        if (s_pk is None) != (t_pk is None):
            return True
        if s_pk is None and t_pk is None:
            return False

        # 对比主键列
        s_cols = [(c[0].lower(), c[1]) for c in s_pk.columns]
        t_cols = [(c[0].lower(), c[1]) for c in t_pk.columns]
        return s_cols != t_cols

    @staticmethod
    def _split_sql_batches(sql: str) -> List[str]:
        """将可能包含 GO 批处理分隔符的脚本切分为独立的 SQL 语句"""
        if not sql or not sql.strip():
            return []
        import re
        batches = [b.strip() for b in re.split(r'^\s*GO\s*$', sql.strip(), flags=re.MULTILINE | re.IGNORECASE) if b.strip()]
        return batches if batches else [sql.strip()]

    @staticmethod
    def generate_create_view(view: ViewMetadata) -> List[str]:
        """
        生成创建/更新视图的 SQL 脚本
        SQL Server 建议使用 CREATE OR ALTER VIEW (2016 SP1+) 或先 DROP 后 CREATE
        """
        statements = []
        drop_sql = f"IF OBJECT_ID('[{view.schema}].[{view.name}]', 'V') IS NOT NULL DROP VIEW [{view.schema}].[{view.name}];"
        statements.append(drop_sql)
        statements.extend(DDLGenerator._split_sql_batches(view.definition))

        if view.description:
            desc_val = view.description.replace("'", "''")
            comment_sql = f"""
                EXEC sys.sp_addextendedproperty 
                    @name = N'MS_Description', 
                    @value = N'{desc_val}', 
                    @level0type = N'SCHEMA', @level0name = N'{view.schema}', 
                    @level1type = N'VIEW', @level1name = N'{view.name}';
            """
            statements.append(comment_sql.strip())
        return statements

    @staticmethod
    def generate_create_view_from_sql(schema: str, name: str, custom_sql: str, description: Optional[str] = None) -> List[str]:
        """
        基于自定义调整后的 SQL 语句生成创建/更新视图的脚本
        """
        statements = []
        drop_sql = f"IF OBJECT_ID('[{schema}].[{name}]', 'V') IS NOT NULL DROP VIEW [{schema}].[{name}];"
        statements.append(drop_sql)
        statements.extend(DDLGenerator._split_sql_batches(custom_sql))

        if description:
            desc_val = description.replace("'", "''")
            comment_sql = f"""
                EXEC sys.sp_addextendedproperty 
                    @name = N'MS_Description', 
                    @value = N'{desc_val}', 
                    @level0type = N'SCHEMA', @level0name = N'{schema}', 
                    @level1type = N'VIEW', @level1name = N'{name}';
            """
            statements.append(comment_sql.strip())
        return statements

