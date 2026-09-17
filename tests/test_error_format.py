import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.connection import format_connection_error, test_sql_connection


class TestErrorFormatting(unittest.TestCase):

    def test_login_failed_password_error(self):
        # pymssql error 18456
        e = Exception(((18456, b"DB-Lib error message 20018, severity 14:\nGeneral SQL Server error: Check messages from the SQL Server\nDB-Lib error message 20002, severity 9:\nAdaptive Server connection failed (127.0.0.1:1433)\n"),))
        msg = format_connection_error(e, {"host": "192.168.1.100", "port": 1433, "username": "sa"})
        self.assertTrue(msg.startswith("链接失败或者密码错误"))
        self.assertIn("sa", msg)
        self.assertIn("身份验证", msg)

    def test_login_failed_pyodbc(self):
        e = Exception("('28000', '[28000] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]Login failed for user \\'sa\\'. (18456) (SQLDriverConnect)')")
        msg = format_connection_error(e, {"host": "127.0.0.1", "username": "sa"})
        self.assertTrue(msg.startswith("链接失败或者密码错误"))

    def test_connection_refused_10061(self):
        e = Exception(((20009, b"DB-Lib error message 20009, severity 9:\nUnable to connect: Adaptive Server is unavailable or does not exist (127.0.0.1:1433)\nNet-Lib error during Connection refused (10061)\n"),))
        msg = format_connection_error(e, {"host": "127.0.0.1", "port": 1433})
        self.assertTrue(msg.startswith("链接失败或者密码错误"))
        self.assertIn("连接被拒绝", msg)
        self.assertIn("TCP/IP", msg)

    def test_connection_timeout_10060(self):
        e = Exception(((20009, b"DB-Lib error message 20009, severity 9:\nUnable to connect: TDS server is unavailable or does not exist (192.168.1.200)\nNet-Lib error during Unknown error (10060)\n"),))
        msg = format_connection_error(e, {"host": "192.168.1.200", "port": 1433})
        self.assertTrue(msg.startswith("链接失败或者密码错误"))
        self.assertIn("超时", msg)
        self.assertIn("防火墙", msg)

    def test_database_not_found_4060(self):
        e = Exception(((4060, b"Cannot open database 'NonExistentDB' requested by the login. The login failed."),))
        msg = format_connection_error(e, {"host": "127.0.0.1", "database": "NonExistentDB"})
        self.assertTrue(msg.startswith("链接失败或者密码错误"))
        self.assertIn("NonExistentDB", msg)

    def test_test_sql_connection_with_mock(self):
        with patch("pymssql.connect") as mock_conn:
            mock_conn.side_effect = Exception(((18456, b"Login failed for user 'admin'"),))
            success, msg = test_sql_connection({"host": "127.0.0.1", "port": 1433, "username": "admin", "password": "wrong"})
            self.assertFalse(success)
            self.assertTrue(msg.startswith("链接失败或者密码错误"))
            self.assertIn("admin", msg)

    def test_test_sql_connection_with_refusal(self):
        with patch("pymssql.connect") as mock_conn:
            mock_conn.side_effect = Exception(((20009, b"Unable to connect: Adaptive Server is unavailable or does not exist. Net-Lib error: (10061)"),))
            success, msg = test_sql_connection({"host": "127.0.0.1", "port": 1433, "username": "sa", "password": "123"})
            self.assertFalse(success)
            self.assertTrue(msg.startswith("链接失败或者密码错误"))
            self.assertIn("连接被拒绝", msg)


if __name__ == "__main__":
    unittest.main()
