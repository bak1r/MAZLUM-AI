"""
Read-only database intelligence tools.
CRITICAL: No write, update, delete, drop, alter, truncate operations.
"""
import logging
import re
from typing import Any, Optional

log = logging.getLogger("seriai.tools.db")

# Forbidden SQL patterns - these NEVER execute
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|"
    r"EXEC|EXECUTE|INTO\s+OUTFILE|LOAD\s+DATA|SET\s+|CALL)\b",
    re.IGNORECASE,
)


class ReadOnlyDB:
    """
    Read-only database access layer.
    Enforces safety at multiple levels:
    1. SQL pattern check (blocks dangerous keywords)
    2. Read-only connection (if supported by driver)
    3. Row limit
    4. Query timeout
    """

    def __init__(self, config):
        self.config = config
        self._engine = None
        self._connected = False

    def connect(self) -> bool:
        """Establish read-only database connection."""
        if not self.config.database.engine:
            log.warning("No database engine configured.")
            return False

        try:
            from sqlalchemy import create_engine, text

            url = self._build_url()
            connect_args = {}

            # Enforce read-only at driver level where possible
            if self.config.database.engine == "postgresql":
                connect_args["options"] = "-c default_transaction_read_only=on"
            elif self.config.database.engine == "mysql":
                connect_args["read_only"] = True
            elif self.config.database.engine == "sqlite":
                url += "?mode=ro"

            self._engine = create_engine(
                url,
                connect_args=connect_args,
                pool_size=2,
                max_overflow=1,
                pool_timeout=self.config.database.query_timeout_sec,
            )
            self._connected = True
            log.info(f"Database connected: {self.config.database.engine}://{self.config.database.user}@{self.config.database.host}:{self.config.database.port}/{self.config.database.name}")
            return True

        except Exception as e:
            log.error(f"Database connection failed: {e}")
            return False

    def _build_url(self) -> str:
        """Build SQLAlchemy connection URL. Password is URL-encoded for safety."""
        from urllib.parse import quote_plus
        c = self.config.database
        pw = quote_plus(c.password) if c.password else ""
        if c.engine == "sqlite":
            return f"sqlite:///{c.name}"
        elif c.engine == "postgresql":
            return f"postgresql://{c.user}:{pw}@{c.host}:{c.port}/{c.name}"
        elif c.engine in ("mysql", "mariadb"):
            return f"mysql+pymysql://{c.user}:{pw}@{c.host}:{c.port}/{c.name}"
        elif c.engine == "mssql":
            return f"mssql+pyodbc://{c.user}:{pw}@{c.host}:{c.port}/{c.name}?driver=ODBC+Driver+17+for+SQL+Server"
        else:
            raise ValueError(f"Unsupported DB engine: {c.engine}")

    def query(self, sql: str) -> dict:
        """
        Execute a read-only SQL query.
        Returns: {"columns": [...], "rows": [...], "row_count": int}
        """
        if not self._connected:
            return {"error": "Database not connected"}

        # Safety check
        if _FORBIDDEN.search(sql):
            return {"error": "Bu sorgu yasak bir işlem içeriyor. Sadece SELECT sorguları çalıştırılabilir."}

        # Must start with SELECT, WITH, SHOW, DESCRIBE, EXPLAIN
        sql_stripped = sql.strip().upper()
        if not sql_stripped.startswith(("SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN")):
            return {"error": "Sadece SELECT/SHOW/DESCRIBE sorguları çalıştırılabilir."}

        try:
            from sqlalchemy import text
            timeout_sec = self.config.database.query_timeout_sec
            with self._engine.connect() as conn:
                # Set statement timeout at connection level
                if self.config.database.engine == "postgresql":
                    conn.execute(text(f"SET statement_timeout = '{timeout_sec * 1000}'"))
                elif self.config.database.engine in ("mysql", "mariadb"):
                    conn.execute(text(f"SET max_execution_time = {timeout_sec * 1000}"))
                result = conn.execute(text(sql))
                columns = list(result.keys())
                rows = [dict(zip(columns, row)) for row in result.fetchmany(self.config.database.max_query_rows)]
                return {
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "truncated": len(rows) >= self.config.database.max_query_rows,
                }
        except Exception as e:
            error_msg = str(e)
            # Sanitize: don't leak connection details in error messages
            if "password" in error_msg.lower() or "@" in error_msg:
                error_msg = "Veritabanı bağlantı hatası."
            return {"error": f"Sorgu hatası: {error_msg}"}

    def get_schema(self) -> dict:
        """Discover database schema (tables, columns, types)."""
        if not self._connected:
            return {"error": "Database not connected"}

        try:
            from sqlalchemy import inspect
            inspector = inspect(self._engine)
            tables = {}
            for table_name in inspector.get_table_names():
                columns = []
                for col in inspector.get_columns(table_name):
                    columns.append({
                        "name": col["name"],
                        "type": str(col["type"]),
                        "nullable": col.get("nullable", True),
                    })
                # Get foreign keys
                fks = []
                for fk in inspector.get_foreign_keys(table_name):
                    fks.append({
                        "columns": fk["constrained_columns"],
                        "references": f"{fk['referred_table']}.{fk['referred_columns']}",
                    })
                tables[table_name] = {"columns": columns, "foreign_keys": fks}
            return {"tables": tables, "table_count": len(tables)}
        except Exception as e:
            return {"error": f"Schema keşif hatası: {str(e)}"}

    def describe_table(self, table_name: str) -> dict:
        """Get detailed info about a specific table."""
        if not self._connected:
            return {"error": "Database not connected"}

        # Table name validation — prevent SQL injection
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name):
            return {"error": f"Geçersiz tablo adı: {table_name}"}

        try:
            from sqlalchemy import inspect
            inspector = inspect(self._engine)

            # Verify table exists in schema
            existing_tables = inspector.get_table_names()
            if table_name not in existing_tables:
                return {"error": f"Tablo bulunamadı: {table_name}"}

            columns = inspector.get_columns(table_name)
            fks = inspector.get_foreign_keys(table_name)
            pk = inspector.get_pk_constraint(table_name)
            indexes = inspector.get_indexes(table_name)

            # Get row count (safe — table_name validated above)
            count_result = self.query(f"SELECT COUNT(*) as cnt FROM {table_name}")
            row_count = count_result.get("rows", [{}])[0].get("cnt", "?")

            return {
                "table": table_name,
                "columns": [{"name": c["name"], "type": str(c["type"]), "nullable": c.get("nullable")} for c in columns],
                "primary_key": pk,
                "foreign_keys": [{"cols": fk["constrained_columns"], "ref": f"{fk['referred_table']}"} for fk in fks],
                "indexes": [{"name": i["name"], "columns": i["column_names"]} for i in indexes],
                "row_count": row_count,
            }
        except Exception as e:
            return {"error": f"Tablo bilgisi hatası: {str(e)}"}

    def close(self):
        """Close database connection."""
        if self._engine:
            self._engine.dispose()
            self._connected = False
            log.info("Database connection closed.")
