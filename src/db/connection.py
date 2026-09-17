import sys
import re
from typing import Dict, Any, List, Optional, Tuple


def parse_db_exception(exc: Exception) -> Tuple[Optional[int], str]:
    """从数据库连接异常对象中递归提取错误码和完整文本描述"""
    code = None
    text_parts = []

    def _extract(obj):
        nonlocal code
        if isinstance(obj, (tuple, list)):
            for item in obj:
                _extract(item)
        elif isinstance(obj, int):
            if code is None and obj > 0:
                code = obj
        elif isinstance(obj, bytes):
            for enc in ("utf-8", "gbk", "latin1"):
                try:
                    text_parts.append(obj.decode(enc))
                    break
                except Exception:
                    continue
        elif isinstance(obj, str):
            text_parts.append(obj)
        elif hasattr(obj, "args") and obj.args:
            _extract(obj.args)
        else:
            text_parts.append(str(obj))

    _extract(exc)
    full_text = " ".join(text_parts)

    match_code = re.search(r'\b(18456|4060|20009|20002|10061|10060|10054|28000|08001)\b', full_text)
    if match_code:
        found = int(match_code.group(1))
        if code is None or code in (20018, 20002, 20009):
            if found in (18456, 4060):
                code = found
            elif code is None:
                code = found

    return code, full_text


def format_connection_error(
    exc: Exception,
    ds_config: Optional[Dict[str, Any]] = None,
    database: Optional[str] = None
) -> str:
    """格式化数据库连接异常为清晰明确的中文提示（统一提示为：链接失败或者密码错误）"""
    ds_config = ds_config or {}
    host = ds_config.get("host", "127.0.0.1")
    port = ds_config.get("port", 1433)
    user = ds_config.get("username", "")
    target_db = database or ds_config.get("database") or "默认库"
    auth_type = ds_config.get("auth_type", "sql")

    code, full_text = parse_db_exception(exc)
    lower_text = full_text.lower()

    # 1. 指定数据库不存在或不可访问 (SQL Server 4060)
    if code == 4060 or "4060" in lower_text or "cannot open database" in lower_text:
        return (
            f"链接失败或者密码错误：指定的数据库 [{target_db}] 不存在或当前账号无访问权限。\n"
            f"• 请检查数据库名称是否拼写正确；\n"
            f"• 请确认当前账号在目标实例上是否已被授予访问该数据库的权限。"
        )

    # 2. 密码错误 / 账号认证失败 (SQL Server 18456, ODBC 28000)
    if (
        code == 18456
        or "18456" in lower_text
        or "login failed for user" in lower_text
        or "28000" in lower_text
        or ("login failed" in lower_text and "cannot open database" not in lower_text)
    ):
        user_hint = f"账号 [{user}] " if user else ""
        return (
            f"链接失败或者密码错误：{user_hint}登录身份验证未通过。\n"
            f"• 密码或账号核对：请检查登录密码及用户名是否输入正确（注意区分英文字母大小写）；\n"
            f"• 认证模式核对：若密码无误，请确认 SQL Server 已开启“SQL Server 和 Windows 身份验证模式”（混合身份验证）；\n"
            f"• 状态与权限：请确认该账号未被禁用/锁定，且具有访问当前数据库的权限。"
        )

    # 3. Windows 认证失败
    if auth_type == "windows" and any(k in lower_text for k in ["sspi", "trusted_connection", "access denied"]):
        return (
            f"链接失败或者密码错误：Windows 身份认证未通过。\n"
            f"• 请检查当前 Windows 系统登录用户是否有权访问目标 SQL Server；\n"
            f"• 或改用“SQL Server 身份验证”并输入对应的账号密码。"
        )

    # 4. 网络/端口拒绝 (10061 Connection Refused)
    if "10061" in lower_text or "connection refused" in lower_text:
        return (
            f"链接失败或者密码错误：无法连接到目标服务器 [{host}:{port}]（连接被拒绝）。\n"
            f"• 服务运行状态：请检查目标机器上的 SQL Server 数据库服务是否已经启动；\n"
            f"• 网络协议配置：请在“SQL Server 配置管理器”中确认 TCP/IP 协议已启用，并确认监听端口为 {port}。"
        )

    # 5. 连接超时 (10060 Timed out)
    if "10060" in lower_text or "timed out" in lower_text or "timeout" in lower_text:
        return (
            f"链接失败或者密码错误：连接目标服务器 [{host}:{port}] 超时。\n"
            f"• 网络与地址核对：请检查服务器 IP 地址或主机名是否正确、能否正常 ping 通；\n"
            f"• 防火墙规则：请检查目标服务器的 Windows 防火墙是否放行了 {port} 端口的入站连接。"
        )

    # 6. 服务器不可达 / 找不到服务器 (20009 / 20002)
    if code in (20009, 20002) or any(k in lower_text for k in ["unavailable or does not exist", "adaptive server connection failed"]):
        return (
            f"链接失败或者密码错误：无法找到或连接到服务器 [{host}:{port}]。\n"
            f"• 请核对服务器 IP 地址与端口是否正确；\n"
            f"• 请确认目标 SQL Server 服务正常运行且网络可达。"
        )

    # 7. 其他异常，去除 DB-Lib / Net-Lib 冗余噪音
    cleaned = re.sub(r"DB-Lib error message \d+, severity \d+:\s*", "", full_text)
    cleaned = re.sub(r"Net-Lib error during [^:\n]+:\s*", "", cleaned)
    cleaned = re.sub(r"Adaptive Server connection failed \([^)]+\)", "", cleaned)
    cleaned = "\n".join(line.strip() for line in cleaned.splitlines() if line.strip())
    detail = cleaned or str(exc)
    return f"链接失败或者密码错误：{detail}"


class MSSQLConnection:
    """SQL Server 数据库连接管理器"""

    def __init__(self, ds_config: Dict[str, Any], database: Optional[str] = None):
        self.ds_config = ds_config
        # 允许 database 为 None，自动连接该账号的默认数据库（避免非 sa 账号因无 master 库权限而报 18456）
        self.database = database if database is not None else ds_config.get("database")
        self._conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def connect(self):
        host = self.ds_config.get("host", "127.0.0.1")
        port = self.ds_config.get("port", 1433)
        auth_type = self.ds_config.get("auth_type", "sql")
        username = self.ds_config.get("username", "")
        password = self.ds_config.get("password", "")
        extra = self.ds_config.get("extra_params", {})
        login_timeout = extra.get("login_timeout", 10)
        timeout = extra.get("timeout", 60)

        # 尝试使用 pymssql
        try:
            import pymssql
            conn_kwargs = {
                "server": host,
                "port": port,
                "charset": "utf8",
                "login_timeout": login_timeout,
                "timeout": timeout,
                "as_dict": False
            }
            if self.database:
                conn_kwargs["database"] = self.database

            if auth_type == "windows":
                # Windows 身份认证
                self._conn = pymssql.connect(**conn_kwargs)
            else:
                conn_kwargs["user"] = username
                conn_kwargs["password"] = password
                self._conn = pymssql.connect(**conn_kwargs)
            return self._conn
        except ImportError:
            pass
        except Exception as e:
            # 捕获 pymssql 连接异常并向上抛出清晰的中文错误信息
            formatted_msg = format_connection_error(e, self.ds_config, self.database)
            raise ConnectionError(formatted_msg) from e

        # 如果没有 pymssql，尝试 pyodbc 备选
        try:
            import pyodbc
            driver = "{ODBC Driver 17 for SQL Server}"
            target_db = self.database or "master"
            if auth_type == "windows":
                conn_str = f"DRIVER={driver};SERVER={host},{port};DATABASE={target_db};Trusted_Connection=yes;"
            else:
                conn_str = f"DRIVER={driver};SERVER={host},{port};DATABASE={target_db};UID={username};PWD={password};"
            self._conn = pyodbc.connect(conn_str, timeout=login_timeout)
            return self._conn
        except Exception as e:
            formatted_msg = format_connection_error(e, self.ds_config, self.database)
            raise ConnectionError(formatted_msg) from e

    def close(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def query(self, sql: str, params: Optional[Tuple] = None) -> List[Dict[str, Any]]:
        """执行查询并返回字典列表"""
        if not self._conn:
            self.connect()
        cursor = self._conn.cursor()
        try:
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            
            # 处理查询结果，兼容 pymssql 字典模式与普通元组模式
            if hasattr(cursor, 'description') and cursor.description:
                columns = [col[0] if col[0] else f"col_{i}" for i, col in enumerate(cursor.description)]
                rows = cursor.fetchall()
                if rows and isinstance(rows[0], dict):
                    return rows
                else:
                    return [dict(zip(columns, row)) for row in rows]
            return []
        finally:
            cursor.close()

    def execute(self, sql: str, params: Optional[Tuple] = None) -> int:
        """执行 DDL 或 DML"""
        if not self._conn:
            self.connect()
        cursor = self._conn.cursor()
        try:
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            self._conn.commit()
            return cursor.rowcount
        except Exception:
            try:
                self._conn.rollback()
            except Exception:
                pass
            raise
        finally:
            cursor.close()

    def get_databases(self) -> List[str]:
        """获取所有在线非系统数据库列表"""
        sql = """
            SELECT name 
            FROM sys.databases 
            WHERE state_desc = 'ONLINE' 
              AND name NOT IN ('master', 'tempdb', 'model', 'msdb')
            ORDER BY name ASC
        """
        rows = self.query(sql)
        return [r["name"] for r in rows]

    def get_version(self) -> str:
        """获取数据库版本信息"""
        rows = self.query("SELECT @@VERSION AS ver")
        if rows:
            return rows[0].get("ver", "").split("\n")[0]
        return "Unknown SQL Server"


def test_sql_connection(ds_config: Dict[str, Any]) -> Tuple[bool, str]:
    """测试数据库连通性"""
    test_db = ds_config.get("database") or None
    try:
        with MSSQLConnection(ds_config, database=test_db) as conn:
            ver = conn.get_version()
            return True, f"连接成功！\n版本：{ver}"
    except Exception as e:
        err_msg = str(e)
        if not err_msg.startswith("链接失败或者密码错误"):
            err_msg = format_connection_error(e, ds_config, test_db)
        return False, err_msg


test_sql_connection.__test__ = False

