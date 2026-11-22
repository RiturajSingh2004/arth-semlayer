"""MCP server: the only surface agents touch."""
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
    def list_metrics() -> list[dict]:
        """List certified metrics, their dimensions and versions."""
        return svc.list_metrics()

    @mcp.tool()
    def describe_metric(name: str) -> dict:
        """Definition, owner, version and lineage of one certified metric."""
        return guard(svc.describe_metric, name)

    @mcp.tool()
    def query_metric(metric: str, as_role: str, dims: list[str] | None = None,
                     filters: dict | None = None, entity_keys: list | None = None) -> dict:
        """Compute a certified metric. Cite name and version in answers. Abstain if none matches."""
        return guard(svc.query_metric, metric, dims, filters, as_role, entity_keys)

    @mcp.tool()
    def describe_graph() -> dict:
        """List entities, their filterable columns and relationship names. Call before traverse_graph."""
        return svc.describe_graph()

    @mcp.tool()
    def traverse_graph(start: str, path: list[str], as_role: str,
                       filters: dict | None = None) -> dict:
        """Entity keys reachable from `start` along relationship names (see describe_graph).
        filters example: {"Order": {"status": "returned"}}. Pass the keys as entity_keys to query_metric."""
        return guard(svc.traverse_graph, start, path, filters, as_role)

    @mcp.tool()
    def report_gap(question: str, as_role: str = "", reason: str = "") -> dict:
        """Call when no certified metric can answer. question = the user's question verbatim;
        put any explanation in reason."""
        return svc.report_gap(question, as_role, reason)

    @mcp.tool()
    def report_issue(metric: str, note: str, as_role: str = "") -> dict:
        """Report a suspected wrong or confusing result."""
        return svc.report_issue(metric, note, as_role)

    return mcp
