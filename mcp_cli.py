import argparse
import json

from mcp_client import call_tool, configured_servers, list_tools


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect and call configured MCP servers.")
    parser.add_argument("server", nargs="?", help="Configured server name")
    parser.add_argument("--call", metavar="TOOL", help="Call a tool by name")
    parser.add_argument("--arguments", default="{}", help="Tool arguments as JSON")
    parser.add_argument("--approve", action="store_true", help="Approve a write-capable tool call")
    args = parser.parse_args()
    if not args.server:
        print(json.dumps({"servers": configured_servers()}, indent=2))
        return
    if args.call:
        print(json.dumps(call_tool(args.server, args.call, json.loads(args.arguments), approve=args.approve), indent=2))
    else:
        print(json.dumps(list_tools(args.server), indent=2))


if __name__ == "__main__":
    main()
