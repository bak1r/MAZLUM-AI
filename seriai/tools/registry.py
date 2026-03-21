"""
Tool registry.
Tools are registered by domain. Only requested tools are loaded per request.
This prevents loading all 40+ tool schemas into every LLM call.
"""
import logging
from typing import Any, Callable, Optional
from dataclasses import dataclass, field

log = logging.getLogger("seriai.tools.registry")


@dataclass
class ToolDef:
    """Tool definition."""
    name: str
    description: str
    domain: str                    # crm, support, general, engineering, etc.
    parameters: dict               # JSON schema for tool input
    handler: Callable              # actual function to call
    requires_db: bool = False
    requires_auth: bool = False


class ToolRegistry:
    """
    Central tool registry.
    Tools register themselves. Brain requests only needed tools per domain.
    """

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef):
        """Register a tool."""
        self._tools[tool.name] = tool
        log.debug(f"Tool registered: {tool.name} (domain={tool.domain})")

    def get_schemas(self, tool_names: list) -> list:
        """
        Get Anthropic-compatible tool schemas for selected tools only.
        This is the key token optimization - don't send all schemas every time.
        """
        schemas = []
        for name in tool_names:
            tool = self._tools.get(name)
            if tool is None:
                continue
            schemas.append({
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            })
        return schemas

    def get_all_schemas_for_domain(self, domain: str) -> list:
        """Get all tool schemas for a domain."""
        tool_names = [t.name for t in self._tools.values() if t.domain == domain or t.domain == "general"]
        return self.get_schemas(tool_names)

    def execute(self, tool_name: str, params: dict) -> Any:
        """Execute a tool by name with basic parameter validation."""
        tool = self._tools.get(tool_name)
        if tool is None:
            return {"error": f"Unknown tool: {tool_name}"}
        if not isinstance(params, dict):
            return {"error": f"Tool params must be dict, got {type(params).__name__}"}
        # Check required parameters
        required = tool.parameters.get("required", [])
        missing = [p for p in required if p not in params]
        if missing:
            return {"error": f"Missing required params for {tool_name}: {missing}"}
        try:
            result = tool.handler(**params)
            return result
        except TypeError as e:
            log.error(f"Tool {tool_name} parameter error: {e}")
            return {"error": f"Parametre hatası: {tool_name} beklenmeyen parametreler aldı."}
        except Exception as e:
            log.error(f"Tool {tool_name} failed: {e}")
            return {"error": f"Tool hatası: {tool_name} çalıştırılamadı."}

    def list_tools(self, domain: Optional[str] = None) -> list[str]:
        """List registered tool names, optionally filtered by domain."""
        if domain:
            return [t.name for t in self._tools.values() if t.domain == domain]
        return list(self._tools.keys())

    @property
    def count(self) -> int:
        return len(self._tools)
