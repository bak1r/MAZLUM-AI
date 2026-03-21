"""
Common/general tools available in all domains.
"""
import logging
from seriai.tools.registry import ToolDef

log = logging.getLogger("seriai.tools.common")


def web_search(query: str, max_results: int = 5) -> dict:
    """Search the web using DuckDuckGo."""
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
        return {"results": results, "count": len(results)}
    except Exception as e:
        return {"error": str(e)}


def get_web_search_tool() -> ToolDef:
    return ToolDef(
        name="web_search",
        description="Web'de arama yap. Güncel bilgi, haber, dokümantasyon bulmak için kullan.",
        domain="general",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Arama sorgusu"},
                "max_results": {"type": "integer", "description": "Maksimum sonuç sayısı", "default": 5},
            },
            "required": ["query"],
        },
        handler=web_search,
    )


def register_common_tools(registry):
    """Register all common tools."""
    registry.register(get_web_search_tool())
