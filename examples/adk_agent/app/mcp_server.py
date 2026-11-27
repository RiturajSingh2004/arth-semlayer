# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Governed stdio MCP server: exposes certified metric and graph tools only."""

from typing import Any

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as Server

from app.semlayer.config import Config
from app.semlayer.registry import Registry
from app.semlayer.service import Refusal, Service
from app.semlayer.warehouse import make_warehouse


def build_server(svc: Service) -> Server:
    """Build the governed MCP server exposing only certified semantic tools."""
    mcp = Server("certified-bigquery-mcp")

    def guard(fn: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except Refusal as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def list_metrics() -> list[dict[str, Any]]:
        """List certified metrics, their allowed dimensions, and versions."""
        return svc.list_metrics()

    @mcp.tool()
    def describe_metric(name: str) -> dict[str, Any]:
        """Return formula, owner, version, and lineage of a certified metric."""
        return guard(svc.describe_metric, name)

    @mcp.tool()
    def query_metric(
        metric: str,
        as_role: str,
        dims: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        entity_keys: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Compute a certified metric under RBAC policy. Cite name and version."""
        return guard(
            svc.query_metric, metric, dims, filters, as_role, entity_keys
        )

    @mcp.tool()
    def describe_graph() -> dict[str, Any]:
        """List certified entities, filterable columns, and relationship names."""
        return svc.describe_graph()

    @mcp.tool()
    def traverse_graph(
        start: str,
        path: list[str],
        as_role: str,
        filters: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Traverse FK relationships from start along path and return entity keys."""
        return guard(svc.traverse_graph, start, path, filters, as_role)

    @mcp.tool()
    def report_gap(
        question: str, as_role: str = "", reason: str = ""
    ) -> dict[str, bool]:
        """Record an unanswerable question verbatim when no certified metric fits."""
        return svc.report_gap(question, as_role, reason)

    @mcp.tool()
    def report_issue(
        metric: str, note: str, as_role: str = ""
    ) -> dict[str, bool]:
        """Report a suspected discrepancy or confusing metric result."""
        return svc.report_issue(metric, note, as_role)

    return mcp


def create_default_server(cfg: Config | None = None) -> Server:
    """Create a governed MCP server configured from environment variables."""
    resolved = cfg or Config.from_env()
    reg = Registry(
        root=resolved.registry_dir,
        state_dir=resolved.state_dir or None,
    )
    warehouse = make_warehouse(resolved, read_only=True)
    return build_server(Service(reg, warehouse))


def main() -> None:
    """Run the governed semantic MCP server over stdio."""
    create_default_server().run()


if __name__ == "__main__":
    main()
