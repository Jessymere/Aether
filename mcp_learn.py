import argparse
import json

from mcp_client import call_tool
from mcp_learning import save_approved_facts, summarize_observation


def main() -> None:
    parser = argparse.ArgumentParser(description="Review and save durable facts from an MCP tool result.")
    parser.add_argument("server")
    parser.add_argument("tool")
    parser.add_argument("--arguments", default="{}", help="Tool arguments as JSON")
    parser.add_argument("--no-graph", action="store_true", help="Save only to memory.md")
    args = parser.parse_args()
    output = call_tool(args.server, args.tool, json.loads(args.arguments))
    facts = summarize_observation(args.server, args.tool, output)
    print(json.dumps(facts, indent=2))
    if not facts:
        print("No durable facts proposed.")
        return
    answer = input("Save these facts to long-term memory? [y/N] ").strip().lower()
    if answer not in {"y", "yes"}:
        print("Nothing saved.")
        return
    count = save_approved_facts(facts, f"{args.server}/{args.tool}", graph=not args.no_graph)
    print(f"Saved {count} new fact(s).")


if __name__ == "__main__":
    main()
