"""Smoke-test the MCP server over stdio, the same way Claude Desktop starts it.
Run from the project root:
  uv run python scripts/mcp_check.py [--warehouse duckdb|bigquery] [--config semlayer.yaml]
"""
import argparse
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def show(result) -> str:
    return " ".join(getattr(c, "text", str(c)) for c in result.content)[:300]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--warehouse", choices=["duckdb", "bigquery"], default=None)
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    env = dict(os.environ)
    if args.warehouse:
        env["SEMLAYER_WAREHOUSE"] = args.warehouse
    cli_args = ["-m", "semlayer.cli"]
    if args.config:
        cli_args += ["--config", args.config]
    cli_args.append("serve")

    params = StdioServerParameters(command=sys.executable, args=cli_args, env=env)
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
        for name, tool_args in calls:
            r = await s.call_tool(name, tool_args)
            print(f"\n{name} {json.dumps(tool_args)[:90]}\n  -> {show(r)}")


if __name__ == "__main__":
    asyncio.run(main())
