from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from .connection import MSSQLConnection


@dataclass
class ColumnMetadata:
    name: str
    data_type: str
    max_length: int
    precision: int
    scale: int
    is_nullable: bool
    is_identity: bool = False
    identity_seed: int = 1
    identity_increment: int = 1
    default_definition: Optional[str] = None
    description: Optional[str] = None

    def get_sql_type(self) -> str:
        t = self.data_type.lower()
        if t in ("varchar", "char", "varbinary", "binary"):
            if self.max_length == -1:
                return f"{t.upper()}(MAX)"
            return f"{t.upper()}({self.max_length})"
        elif t in ("nvarchar", "nchar"):
            if self.max_length == -1:
                return f"{t.upper()}(MAX)"
            return f"{t.upper()}({self.max_length // 2})"
        elif t in ("decimal", "numeric"):
            return f"{t.upper()}({self.precision},{self.scale})"
        elif t in ("time", "datetime2", "datetimeoffset"):
            return f"{t.upper()}({self.scale})"
        return t.upper()


@dataclass
class IndexMetadata:
    name: str
    is_unique: bool
    is_primary_key: bool
    type_desc: str  # CLUSTERED, NONCLUSTERED
    columns: List[Tuple[str, bool]] = field(default_factory=list)  # (column_name, is_descending)
    included_columns: List[str] = field(default_factory=list)


@dataclass
class ForeignKeyMetadata:
    name: str
    schema: str
    parent_table: str
    referenced_schema: str
    referenced_table: str
    columns: List[Tuple[str, str]] = field(default_factory=list)  # (parent_col, ref_col)
    delete_referential_action_desc: str = "NO_ACTION"
    update_referential_action_desc: str = "NO_ACTION"


@dataclass
class TableMetadata:
    schema: str
    name: str
    columns: List[ColumnMetadata] = field(default_factory=list)
    primary_key: Optional[IndexMetadata] = None
    indexes: List[IndexMetadata] = field(default_factory=list)
    foreign_keys: List[ForeignKeyMetadata] = field(default_factory=list)
    description: Optional[str] = None
    row_count: int = 0

    @property
    def full_name(self) -> str:
        return f"[{self.schema}].[{self.name}]"

    def get_column(self, col_name: str) -> Optional[ColumnMetadata]:
        for col in self.columns:
            if col.name.lower() == col_name.lower():
                return col
        return None


@dataclass
class ViewMetadata:
    schema: str
    name: str
    definition: str
    description: Optional[str] = None

    @property
    def full_name(self) -> str:
        return f"[{self.schema}].[{self.name}]"


def _safe_int(val: Any, default: int = 1) -> int:
    """安全解析整型数值，兼容 bytes（FreeTDS/pymssql 对 sql_variant 返回二进制）、int、str 及 None"""
    if val is None:
        return default
    if isinstance(val, int):
        return val
    if isinstance(val, bytes):
        try:
            return int.from_bytes(val, byteorder="little")
        except Exception:
            return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _safe_str(val: Any) -> Optional[str]:
    """安全解析字符串，兼容 bytes 各种编码及 None"""
    if val is None:
        return None
    if isinstance(val, str):
        return val
    if isinstance(val, bytes):
        for enc in ("utf-8", "gbk", "latin1"):
            try:
                return val.decode(enc)
            except Exception:
                continue
        return str(val)
    return str(val)


class DatabaseMetadataExtractor:
    """SQL Server 数据库元数据批量抽取器"""

    def __init__(self, conn: MSSQLConnection):
        self.conn = conn

    def list_objects(self) -> List[Dict[str, Any]]:
        """获取当前数据库中所有用户表和视图的基本信息列表"""
        sql = """
            -- 获取所有用户表及行数估算
            SELECT 
                s.name AS [schema],
                t.name AS [name],
                'TABLE' AS [type],
                ISNULL(p.rows, 0) AS [row_count]
            FROM sys.tables t
            INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
            LEFT JOIN (
                SELECT object_id, SUM(rows) AS rows
                FROM sys.partitions
                WHERE index_id IN (0, 1)
                GROUP BY object_id
            ) p ON t.object_id = p.object_id
            WHERE t.is_ms_shipped = 0
            
            UNION ALL
            
            -- 获取所有用户视图
            SELECT 
                s.name AS [schema],
                v.name AS [name],
                'VIEW' AS [type],
                0 AS [row_count]
            FROM sys.views v
            INNER JOIN sys.schemas s ON v.schema_id = s.schema_id
            WHERE v.is_ms_shipped = 0
            ORDER BY [type] DESC, [name] ASC
        """
        return self.conn.query(sql)

    def extract_table(self, schema_name: str, table_name: str) -> TableMetadata:
        """提取指定单表的详细元数据"""
        # 1. 查询列信息 (对 sql_variant 类型字段进行显式 CAST，防止驱动返回 raw bytes)
        col_sql = """
            SELECT 
                c.name,
                tp.name AS data_type,
                c.max_length,
                c.precision,
                c.scale,
                c.is_nullable,
                c.is_identity,
                CAST(ISNULL(ic.seed_value, 1) AS BIGINT) AS identity_seed,
                CAST(ISNULL(ic.increment_value, 1) AS BIGINT) AS identity_increment,
                dc.definition AS default_definition,
                CAST(ep.value AS NVARCHAR(MAX)) AS [description]
            FROM sys.columns c
            INNER JOIN sys.tables t ON c.object_id = t.object_id
            INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
            INNER JOIN sys.types tp ON c.user_type_id = tp.user_type_id
            LEFT JOIN sys.identity_columns ic ON c.object_id = ic.object_id AND c.column_id = ic.column_id
            LEFT JOIN sys.default_constraints dc ON c.default_object_id = dc.object_id
            LEFT JOIN sys.extended_properties ep ON ep.major_id = c.object_id AND ep.minor_id = c.column_id AND ep.name = 'MS_Description'
            WHERE s.name = %s AND t.name = %s
            ORDER BY c.column_id ASC
        """
        cols_raw = self.conn.query(col_sql, (schema_name, table_name))
        columns = [
            ColumnMetadata(
                name=r["name"],
                data_type=r["data_type"],
                max_length=r["max_length"],
                precision=r["precision"],
                scale=r["scale"],
                is_nullable=bool(r["is_nullable"]),
                is_identity=bool(r["is_identity"]),
                identity_seed=_safe_int(r["identity_seed"], 1),
                identity_increment=_safe_int(r["identity_increment"], 1),
                default_definition=r["default_definition"],
                description=_safe_str(r["description"])
            )
            for r in cols_raw
        ]

        # 2. 查询索引及主键
        idx_sql = """
            SELECT 
                i.name AS index_name,
                i.is_unique,
                i.is_primary_key,
                i.type_desc,
                c.name AS column_name,
                ic.is_descending_key,
                ic.is_included_column
            FROM sys.indexes i
            INNER JOIN sys.tables t ON i.object_id = t.object_id
            INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
            INNER JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id
            INNER JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id
            WHERE s.name = %s AND t.name = %s AND i.type > 0 -- 排除堆
            ORDER BY i.index_id, ic.key_ordinal
        """
        indexes_raw = self.conn.query(idx_sql, (schema_name, table_name))
        indexes_dict: Dict[str, IndexMetadata] = {}
        for r in indexes_raw:
            idx_name = r["index_name"]
            if idx_name not in indexes_dict:
                indexes_dict[idx_name] = IndexMetadata(
                    name=idx_name,
                    is_unique=bool(r["is_unique"]),
                    is_primary_key=bool(r["is_primary_key"]),
                    type_desc=r["type_desc"],
                    columns=[],
                    included_columns=[]
                )
            if r["is_included_column"]:
                indexes_dict[idx_name].included_columns.append(r["column_name"])
            else:
                indexes_dict[idx_name].columns.append((r["column_name"], bool(r["is_descending_key"])))

        primary_key = None
        other_indexes = []
        for idx in indexes_dict.values():
            if idx.is_primary_key:
                primary_key = idx
            else:
                other_indexes.append(idx)

        # 3. 表注释
        table_desc_sql = """
            SELECT CAST(ep.value AS NVARCHAR(MAX)) AS [description]
            FROM sys.tables t
            INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
            INNER JOIN sys.extended_properties ep ON ep.major_id = t.object_id AND ep.minor_id = 0 AND ep.name = 'MS_Description'
            WHERE s.name = %s AND t.name = %s
        """
        desc_res = self.conn.query(table_desc_sql, (schema_name, table_name))
        table_desc = _safe_str(desc_res[0]["description"]) if desc_res else None

        # 4. 行数
        count_sql = """
            SELECT SUM(rows) AS rows
            FROM sys.partitions p
            INNER JOIN sys.tables t ON p.object_id = t.object_id
            INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
            WHERE s.name = %s AND t.name = %s AND p.index_id IN (0, 1)
        """
        c_res = self.conn.query(count_sql, (schema_name, table_name))
        row_count = c_res[0]["rows"] if c_res and c_res[0]["rows"] is not None else 0

        return TableMetadata(
            schema=schema_name,
            name=table_name,
            columns=columns,
            primary_key=primary_key,
            indexes=other_indexes,
            description=table_desc,
            row_count=row_count
        )

    def extract_view(self, schema_name: str, view_name: str) -> ViewMetadata:
        """提取指定视图的 DDL 定义与注释"""
        sql = """
            SELECT 
                sm.definition,
                CAST(ep.value AS NVARCHAR(MAX)) AS [description]
            FROM sys.views v
            INNER JOIN sys.schemas s ON v.schema_id = s.schema_id
            LEFT JOIN sys.sql_modules sm ON v.object_id = sm.object_id
            LEFT JOIN sys.extended_properties ep ON ep.major_id = v.object_id AND ep.minor_id = 0 AND ep.name = 'MS_Description'
            WHERE s.name = %s AND v.name = %s
        """
        rows = self.conn.query(sql, (schema_name, view_name))
        if not rows:
            raise ValueError(f"未找到视图 [{schema_name}].[{view_name}]")
        r = rows[0]
        definition = r["definition"] or ""
        desc = _safe_str(r["description"])
        return ViewMetadata(
            schema=schema_name,
            name=view_name,
            definition=definition,
            description=desc
        )

    def get_table_foreign_keys_referencing(self, schema_name: str, table_name: str) -> List[Dict[str, Any]]:
        """获取所有引用了此表的外键约束（用于先删后建兜底时解除依赖）"""
        sql = """
            SELECT 
                fk.name AS fk_name,
                s_parent.name AS parent_schema,
                t_parent.name AS parent_table
            FROM sys.foreign_keys fk
            INNER JOIN sys.tables t_ref ON fk.referenced_object_id = t_ref.object_id
            INNER JOIN sys.schemas s_ref ON t_ref.schema_id = s_ref.schema_id
            INNER JOIN sys.tables t_parent ON fk.parent_object_id = t_parent.object_id
            INNER JOIN sys.schemas s_parent ON t_parent.schema_id = s_parent.schema_id
            WHERE s_ref.name = %s AND t_ref.name = %s
        """
        return self.conn.query(sql, (schema_name, table_name))
