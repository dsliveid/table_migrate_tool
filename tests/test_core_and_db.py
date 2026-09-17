import os
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import MagicMock
from src.core.config import AppConfig
from src.core.crypto import PasswordEncryptor
from src.core.storage import AppStorage
from src.db.metadata import (
    TableMetadata, ColumnMetadata, IndexMetadata, ViewMetadata,
    _safe_int, _safe_str, DatabaseMetadataExtractor
)
from src.db.generator import DDLGenerator
from src.db.migrator import MigrationEngine, MigrationTaskConfig, MigrationProgressSignal


class TestCoreAndDB(unittest.TestCase):

    def setUp(self):
        AppConfig.initialize()
        self.storage = AppStorage()

    def test_config_dirs(self):
        self.assertTrue(AppConfig.get_data_dir().exists())
        self.assertTrue(AppConfig.get_logs_dir().exists())
        self.assertTrue(str(AppConfig.get_db_path()).endswith("app.db"))

    def test_crypto(self):
        secret = "MyP@ssw0rd!123"
        enc = PasswordEncryptor.encrypt(secret)
        self.assertNotEqual(secret, enc)
        dec = PasswordEncryptor.decrypt(enc)
        self.assertEqual(secret, dec)

    def test_storage_datasources(self):
        ds_data = {
            "name": "TestDS",
            "host": "127.0.0.1",
            "port": 1433,
            "auth_type": "sql",
            "username": "sa",
            "password": "Password123",
            "extra_params": {"timeout": 30}
        }
        ds_id = self.storage.save_datasource(ds_data)
        self.assertIsNotNone(ds_id)

        fetched = self.storage.get_datasource(ds_id)
        self.assertEqual(fetched["name"], "TestDS")
        self.assertEqual(fetched["password"], "Password123")  # Decrypted automatically
        self.assertEqual(fetched["extra_params"]["timeout"], 30)

        # Update
        fetched["host"] = "192.168.1.50"
        self.storage.save_datasource(fetched)
        updated = self.storage.get_datasource(ds_id)
        self.assertEqual(updated["host"], "192.168.1.50")

        # Delete
        self.assertTrue(self.storage.delete_datasource(ds_id))
        self.assertIsNone(self.storage.get_datasource(ds_id))

    def test_storage_plans(self):
        plan_data = {
            "name": "TestMigrationPlan",
            "description": "Daily sync",
            "source_ds_id": 1,
            "target_ds_id": 2,
            "source_db": "SourceDB",
            "target_db": "TargetDB",
            "strategy": "diff",
            "objects_config": [
                {"name": "Users", "type": "TABLE", "migrate_data": True},
                {
                    "name": "v_ActiveUsers",
                    "type": "VIEW",
                    "migrate_data": False,
                    "custom_sql": "CREATE VIEW [dbo].[v_ActiveUsers] AS SELECT id, name FROM users WHERE active = 1"
                }
            ]
        }
        plan_id = self.storage.save_plan(plan_data)
        self.assertIsNotNone(plan_id)

        fetched = self.storage.get_plan(plan_id)
        self.assertEqual(fetched["name"], "TestMigrationPlan")
        self.assertEqual(len(fetched["objects_config"]), 2)
        self.assertFalse(fetched["objects_config"][1]["migrate_data"])
        self.assertIn("WHERE active = 1", fetched["objects_config"][1]["custom_sql"])

        # Delete
        self.assertTrue(self.storage.delete_plan(plan_id))
        self.assertIsNone(self.storage.get_plan(plan_id))

    def test_ddl_generation(self):
        # Create table metadata
        table = TableMetadata(
            schema="dbo",
            name="Users",
            columns=[
                ColumnMetadata("Id", "int", 4, 10, 0, False, is_identity=True),
                ColumnMetadata("Username", "nvarchar", 100, 0, 0, False, description="User Login Name"),
                ColumnMetadata("Email", "varchar", 255, 0, 0, True),
                ColumnMetadata("CreatedAt", "datetime2", 8, 0, 7, False, default_definition="(getdate())")
            ],
            primary_key=IndexMetadata("PK_Users", is_unique=True, is_primary_key=True, type_desc="CLUSTERED", columns=[("Id", False)]),
            indexes=[
                IndexMetadata("IX_Users_Email", is_unique=False, is_primary_key=False, type_desc="NONCLUSTERED", columns=[("Email", False)])
            ],
            description="System Users Table"
        )

        stmts = DDLGenerator.generate_create_table(table)
        full_sql = "\n".join(stmts)
        self.assertIn("CREATE TABLE [dbo].[Users]", full_sql)
        self.assertIn("[Id] INT IDENTITY(1,1) NOT NULL", full_sql)
        self.assertIn("[Username] NVARCHAR(50) NOT NULL", full_sql)
        self.assertIn("CONSTRAINT [PK_Users] PRIMARY KEY CLUSTERED ([Id] ASC)", full_sql)
        self.assertIn("CREATE NONCLUSTERED INDEX [IX_Users_Email] ON [dbo].[Users] ([Email] ASC)", full_sql)
        self.assertIn("System Users Table", full_sql)
        self.assertIn("User Login Name", full_sql)

    def test_ddl_diff_and_pk_change(self):
        s_table = TableMetadata(
            schema="dbo",
            name="Products",
            columns=[
                ColumnMetadata("Id", "int", 4, 10, 0, False),
                ColumnMetadata("Name", "nvarchar", 200, 0, 0, False),
                ColumnMetadata("Price", "decimal", 9, 18, 2, False)  # Added column
            ],
            primary_key=IndexMetadata("PK_Products", True, True, "CLUSTERED", [("Id", False)])
        )

        t_table = TableMetadata(
            schema="dbo",
            name="Products",
            columns=[
                ColumnMetadata("Id", "int", 4, 10, 0, False),
                ColumnMetadata("Name", "nvarchar", 100, 0, 0, False)  # Length 50 vs 100
            ],
            primary_key=IndexMetadata("PK_Products", True, True, "CLUSTERED", [("Id", False)])
        )

        self.assertFalse(DDLGenerator.is_pk_changed(s_table, t_table))

        diffs = DDLGenerator.generate_diff_statements(s_table, t_table)
        diff_sql = "\n".join(diffs)
        # Should contain ADD [Price]
        self.assertIn("ADD [Price] DECIMAL(18,2)", diff_sql)
        # Should contain ALTER COLUMN [Name]
        self.assertIn("ALTER COLUMN [Name] NVARCHAR(100)", diff_sql)

        # Now test PK change detection
        s_table_new_pk = TableMetadata(
            schema="dbo",
            name="Products",
            columns=[],
            primary_key=IndexMetadata("PK_Products_Composite", True, True, "CLUSTERED", [("Id", False), ("TenantId", False)])
        )
        self.assertTrue(DDLGenerator.is_pk_changed(s_table_new_pk, t_table))

    def test_safe_helpers(self):
        # Test bytes from pymssql sql_variant
        self.assertEqual(_safe_int(b'\x01\x00\x00\x00'), 1)
        self.assertEqual(_safe_int(b'\x01\x00\x00\x00\x00\x00\x00\x00'), 1)
        self.assertEqual(_safe_int(b'\x0a\x00\x00\x00'), 10)
        self.assertEqual(_safe_int(100), 100)
        self.assertEqual(_safe_int("50"), 50)
        self.assertEqual(_safe_int(None, default=1), 1)
        self.assertEqual(_safe_int("invalid", default=1), 1)

        # Test _safe_str
        self.assertEqual(_safe_str("hello"), "hello")
        self.assertEqual(_safe_str(b'test_desc'), "test_desc")
        self.assertEqual(_safe_str("测试".encode("utf-8")), "测试")
        self.assertIsNone(_safe_str(None))

    def test_extract_table_with_variant_bytes(self):
        mock_conn = MagicMock()
        # Mock query return values for columns, indexes, description, row_count
        def mock_query(sql, params=None):
            if "identity_seed" in sql:
                return [{
                    "name": "id",
                    "data_type": "int",
                    "max_length": 4,
                    "precision": 10,
                    "scale": 0,
                    "is_nullable": False,
                    "is_identity": True,
                    "identity_seed": b'\x01\x00\x00\x00',  # sql_variant byte simulation
                    "identity_increment": b'\x01\x00\x00\x00',  # sql_variant byte simulation
                    "default_definition": None,
                    "description": b'User ID column'  # sql_variant byte simulation
                }]
            elif "sys.indexes" in sql:
                return []
            elif "ep.minor_id = 0" in sql:
                return [{"description": b'User table description'}]
            elif "sys.partitions" in sql:
                return [{"rows": 100}]
            return []

        mock_conn.query.side_effect = mock_query
        extractor = DatabaseMetadataExtractor(mock_conn)
        table = extractor.extract_table("dbo", "users")

        self.assertEqual(len(table.columns), 1)
        col = table.columns[0]
        self.assertEqual(col.name, "id")
        self.assertTrue(col.is_identity)
        self.assertEqual(col.identity_seed, 1)
        self.assertEqual(col.identity_increment, 1)
        self.assertEqual(col.description, "User ID column")
        self.assertEqual(table.description, "User table description")
        self.assertEqual(table.row_count, 100)

    def test_view_ddl_custom_sql(self):
        schema = "dbo"
        name = "v_users"
        custom_sql = "CREATE VIEW [dbo].[v_users] AS SELECT id, username FROM users WHERE is_active = 1"
        stmts = DDLGenerator.generate_create_view_from_sql(schema, name, custom_sql, description="Active Users View")
        self.assertEqual(len(stmts), 3)
        self.assertIn("DROP VIEW [dbo].[v_users]", stmts[0])
        self.assertEqual(stmts[1], custom_sql)
        self.assertIn("Active Users View", stmts[2])

    def test_migrator_with_custom_view_sql(self):
        # Test that _migrate_single_view prioritizes custom_sql over extracted definition
        task_config = MigrationTaskConfig(
            source_ds={},
            target_ds={},
            source_db="SourceDB",
            target_db="TargetDB",
            strategy="diff",
            objects=[]
        )
        signals = MigrationProgressSignal()
        engine = MigrationEngine(task_config, signals)

        mock_src_extractor = MagicMock()
        mock_src_extractor.extract_view.return_value = ViewMetadata(
            schema="dbo",
            name="v_remote",
            definition="CREATE VIEW [dbo].[v_remote] AS SELECT * FROM [192.168.230.6].db.dbo.tbl",
            description=None
        )

        mock_tgt_conn = MagicMock()
        executed_sqls = []
        mock_tgt_conn.execute.side_effect = lambda sql: executed_sqls.append(sql)

        # Case 1: with custom_sql
        custom_sql = "CREATE VIEW [dbo].[v_remote] AS SELECT * FROM [192.168.200.45].db.dbo.tbl"
        engine._migrate_single_view(
            mock_src_extractor,
            mock_tgt_conn,
            "dbo",
            "v_remote",
            custom_sql=custom_sql
        )
        self.assertIn(custom_sql, executed_sqls)
        # Should not have called raw definition
        self.assertNotIn("192.168.230.6", "\n".join(executed_sqls))

        # Case 2: without custom_sql (uses extracted view definition)
        executed_sqls.clear()
        engine._migrate_single_view(
            mock_src_extractor,
            mock_tgt_conn,
            "dbo",
            "v_remote",
            custom_sql=None
        )
        self.assertIn("192.168.230.6", "\n".join(executed_sqls))

    def test_connection_query_unnamed_columns(self):
        from src.db.connection import MSSQLConnection
        conn = MSSQLConnection({"host": "127.0.0.1", "port": 1433, "username": "sa", "password": "pwd"})
        mock_cursor = MagicMock()
        # Simulate cursor.description with unnamed column (col[0] is None or empty string)
        mock_cursor.description = [(None, 1, 0, 0, 0, 0, 0), ("named_col", 1, 0, 0, 0, 0, 0)]
        mock_cursor.fetchall.return_value = [(1, "test_val")]
        conn._conn = MagicMock()
        conn._conn.cursor.return_value = mock_cursor

        res = conn.query("SELECT 1, 'test_val' AS named_col")
        self.assertEqual(len(res), 1)
        self.assertIn("col_0", res[0])
        self.assertEqual(res[0]["col_0"], 1)
        self.assertEqual(res[0]["named_col"], "test_val")

    def test_table_exists_query(self):
        task_config = MigrationTaskConfig(
            source_ds={},
            target_ds={},
            source_db="SourceDB",
            target_db="TargetDB",
            strategy="diff",
            objects=[]
        )
        signals = MigrationProgressSignal()
        engine = MigrationEngine(task_config, signals)
        mock_conn = MagicMock()
        mock_conn.query.return_value = [{"exists_flag": 1}]

        exists = engine._table_exists(mock_conn, "dbo", "test_table")
        self.assertTrue(exists)
        args, _ = mock_conn.query.call_args
        sql = args[0]
        self.assertIn("AS [exists_flag]", sql)


if __name__ == "__main__":
    unittest.main()
