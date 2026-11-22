"""Smoke-test the MCP server over stdio, the same way Claude Desktop starts it.
Run from the project root: uv run python scripts/mcp_check.py"""
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def show(result) -> str:
    return " ".join(getattr(c, "text", str(c)) for c in result.content)[:300]


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "semlayer.cli", "serve"])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
        await s.initialize()
        tools = await s.list_tools()
        print("tools:", [t.name for t in getattr(tools, "tools", tools)])
        calls = [
            ("list_metrics", {}),
            ("describe_graph", {}),
            ("query_metric", {"metric": "revenue", "as_role": "finance"}),
            ("query_metric", {"metric": "revenue", "as_role": "eu_analyst"}),
            ("query_metric", {"metric": "customer_count", "as_role": "eu_analyst"}),
            ("traverse_graph", {"start": "Customer", "path": ["HAS_ORDER"], "as_role": "finance",
                                "filters": {"Order": {"status": "returned"}}}),
        ]
        for name, args in calls:
            r = await s.call_tool(name, args)
            print(f"\n{name} {json.dumps(args)[:90]}\n  -> {show(r)}")


asyncio.run(main())