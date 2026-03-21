"""
Database intelligence tools for the tool registry.
"""
from seriai.tools.registry import ToolDef


def create_db_tools(db_instance) -> list[ToolDef]:
    """Create tool definitions that use the given ReadOnlyDB instance."""

    def db_query(sql: str) -> dict:
        return db_instance.query(sql)

    def db_schema() -> dict:
        return db_instance.get_schema()

    def db_describe_table(table_name: str) -> dict:
        return db_instance.describe_table(table_name)

    return [
        ToolDef(
            name="db_query",
            description="Veritabanında read-only SQL sorgusu çalıştır. Sadece SELECT kullanılabilir.",
            domain="crm",
            parameters={
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "SELECT SQL sorgusu"},
                },
                "required": ["sql"],
            },
            handler=db_query,
            requires_db=True,
        ),
        ToolDef(
            name="db_schema",
            description="Veritabanı şemasını keşfet: tablolar, kolonlar, ilişkiler.",
            domain="crm",
            parameters={"type": "object", "properties": {}},
            handler=db_schema,
            requires_db=True,
        ),
        ToolDef(
            name="db_describe_table",
            description="Belirli bir tablonun detaylarını göster: kolonlar, tipler, indexler, satır sayısı.",
            domain="crm",
            parameters={
                "type": "object",
                "properties": {
                    "table_name": {"type": "string", "description": "Tablo adı"},
                },
                "required": ["table_name"],
            },
            handler=db_describe_table,
            requires_db=True,
        ),
    ]
