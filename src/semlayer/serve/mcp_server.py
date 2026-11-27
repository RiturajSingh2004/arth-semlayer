"""MCP server: the only surface agents touch (no raw SQL tool)."""
try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as Server

from .service import Refusal, Service


def build_server(svc: Service) -> Server:
    mcp = Server("arth-semlayer")

    def guard(fn, *a, **k):
        try:
            return fn(*a, **k)
        except Refusal as e:
            return {"error": str(e)}

    @mcp.tool()
    def list_metrics(limit: int | None = None, offset: int = 0) -> list[dict] | dict:
        """List certified metrics, their dimensions and versions (supports optional limit/offset)."""
        return guard(svc.list_metrics, limit=limit, offset=offset)

    @mcp.tool()
    def search_metrics(query: str, k: int = 10) -> list[dict] | dict:
        """Search certified metrics by keyword, dimension or synonym (BM25 over FTS5)."""
        return guard(svc.search_metrics, query=query, k=k)

    @mcp.tool()
    def describe_metric(name: str) -> dict:
        """Definition, owner, version, time/joinable dimensions and lineage of one certified metric."""
        return guard(svc.describe_metric, name)

    @mcp.tool()
    def query_metric(
        metric: str,
        as_role: str = "",
        dims: list[str] | None = None,
        filters: dict | list | None = None,
        entity_keys: list | None = None,
        time_grain: str | None = None,
        time_range: str | list | dict | None = None,
        order_by: str | list[str] | None = None,
        limit: int | None = None,
    ) -> dict:
        """Compute a certified metric (simple, ratio or derived). Cite name and version in answers.
        Omit as_role when server is started with a bound --role."""
        return guard(
            svc.query_metric,
            metric,
            dims,
            filters,
            as_role,
            entity_keys,
            time_grain=time_grain,
            time_range=time_range,
            order_by=order_by,
            limit=limit,
        )

    @mcp.tool()
    def query_metrics(
        metrics: list[str],
        as_role: str = "",
        dims: list[str] | None = None,
        filters: dict | list | None = None,
        entity_keys: list | None = None,
        time_grain: str | None = None,
        time_range: str | list | dict | None = None,
        order_by: str | list[str] | None = None,
        limit: int | None = None,
    ) -> dict:
        """Compute several certified metrics at the same grain without fan-out multiplication."""
        return guard(
            svc.query_metrics,
            metrics,
            dims,
            filters,
            as_role,
            entity_keys,
            time_grain=time_grain,
            time_range=time_range,
            order_by=order_by,
            limit=limit,
        )

    @mcp.tool()
    def describe_graph() -> dict:
        """List entities, their filterable columns and relationship names/cardinalities."""
        return svc.describe_graph()

    @mcp.tool()
    def traverse_graph(start: str, path: list[str], as_role: str = "",
                       filters: dict | None = None) -> dict:
        """Entity keys reachable from `start` along relationship names (see describe_graph).
        filters example: {"Order": {"status": "returned"}}. Pass the keys as entity_keys to query_metric."""
        return guard(svc.traverse_graph, start, path, filters, as_role)

    @mcp.tool()
    def report_gap(question: str, as_role: str = "", reason: str = "") -> dict:
        """Call when no certified metric can answer. question = the user's question verbatim;
        put any explanation in reason."""
        return guard(svc.report_gap, question, as_role, reason)

    @mcp.tool()
    def report_issue(metric: str, note: str, as_role: str = "") -> dict:
        """Report a suspected wrong or confusing result."""
        return guard(svc.report_issue, metric, note, as_role)

    return mcp
