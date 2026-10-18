import asyncio
import json
from pathlib import Path

CONFIG = Path(__file__).with_name("mcp_servers.json")

RISK_WORDS = ("write", "edit", "delete", "remove", "move", "rename", "create", "update", "insert", "execute", "run", "shell", "command", "push", "commit", "send", "overwrite", "append")


def _load(name: str) -> dict:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    try:
        config = data["servers"][name]
        if config.get("enabled", True) is False:
            raise ValueError(f"MCP server '{name}' is disabled")
        return config
    except KeyError as exc:
        raise ValueError(f"MCP server '{name}' is not configured") from exc


def _parameters(config: dict):
    try:
        from mcp import StdioServerParameters
    except ImportError as exc:
        raise RuntimeError('Install MCP support with: pip install "mcp[cli]"') from exc
    if not config.get("command"):
        raise ValueError("This local MCP wrapper currently requires a stdio 'command'")
    return StdioServerParameters(command=config["command"], args=config.get("args", []), env=config.get("env"))


async def _list_tools(name: str) -> list[dict]:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client
    async with stdio_client(_parameters(_load(name))) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as client:
            await client.initialize()
            result = await client.list_tools()
            return [{"name": tool.name, "description": tool.description, "input_schema": tool.inputSchema} for tool in result.tools]


async def _call_tool(server: str, tool: str, arguments: dict, approve: bool) -> list[dict]:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client
    async with stdio_client(_parameters(_load(server))) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as client:
            await client.initialize()
            available = await client.list_tools()
            selected = next((item for item in available.tools if item.name == tool), None)
            if selected is None:
                raise ValueError(f"MCP tool '{tool}' was not found on server '{server}'")
            description = f"{selected.name} {selected.description or ''}".lower()
            if any(word in description for word in RISK_WORDS) and not approve:
                raise PermissionError(f"MCP tool '{tool}' appears write-capable; rerun with --approve after reviewing it")
            result = await client.call_tool(tool, arguments)
            return [{"type": getattr(item, "type", "unknown"), "text": getattr(item, "text", "")} for item in result.content]


def list_tools(server: str) -> list[dict]:
    return asyncio.run(_list_tools(server))


def configured_servers() -> list[str]:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    return sorted(name for name, item in data.get("servers", {}).items() if item.get("enabled", True))


def call_tool(server: str, tool: str, arguments: dict, approve: bool = False) -> list[dict]:
    return asyncio.run(_call_tool(server, tool, arguments, approve))
