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
"""ADK LlmAgent connected to the certification-gated semantic MCP server."""

import os
import sys
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools.mcp_tool.mcp_toolset import (
    McpToolset,
    StdioConnectionParams,
)
from google.genai import types
from mcp import StdioServerParameters

RECIPE_ROOT = Path(__file__).resolve().parent.parent
MCP_CONNECTION_TIMEOUT = 60


def _build_instruction(default_role: str) -> str:
    return f"""\
You are a governed analytics assistant connected to a certification-gated \
semantic layer over MCP. You never write or execute raw SQL; there is no raw \
SQL tool.

Follow these governance rules strictly:
1. Call `list_metrics` (and `describe_metric` when details are needed) before \
answering any quantitative or business-metric question.
2. Compute numbers ONLY with `query_metric`. Cite the certified metric `name` \
and `version` (for example, `revenue (v2)`) in every numerical answer.
3. Pass the caller's role in `as_role`. If the user specifies their role \
(such as `finance` or `eu_analyst`), use that exact role; otherwise default to \
`{default_role}`. Never escalate to a broader role after an access refusal.
4. If several certified metrics could match an ambiguous term (for example \
`active_customers_30d`, `active_customers_completed`, or \
`active_customers_paid`), explain the certified options and ask the user which \
definition they want instead of guessing.
5. For cross-entity cohort questions (for example, revenue from customers with \
completed or returned orders): first call `describe_graph`, then call \
`traverse_graph` to retrieve matching entity keys, and pass those keys as \
`entity_keys` to `query_metric` for a metric linked to that start entity.
6. If no certified metric can answer the question, state clearly that no \
certified definition exists, call `report_gap` with the user's verbatim \
question and your explanation in `reason`, and abstain from inventing numbers.
7. If a returned metric result appears inconsistent or unexpected, call \
`report_issue` rather than working around the semantic layer."""


def create_mcp_toolset() -> McpToolset:
    """Create the stdio McpToolset connected to ``app.mcp_server``."""
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
        env=dict(os.environ),
        cwd=str(RECIPE_ROOT),
    )
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=server_params,
            timeout=MCP_CONNECTION_TIMEOUT,
        )
    )


def create_agent() -> LlmAgent:
    """Create the certification-gated analytics LlmAgent."""
    default_role = (os.getenv("SEMLAYER_DEFAULT_ROLE") or "finance").strip()
    return LlmAgent(
        name="certified_bigquery_mcp_agent",
        model=Gemini(
            model=os.getenv("MODEL_NAME"),
            retry_options=types.HttpRetryOptions(attempts=3),
        ),
        instruction=_build_instruction(default_role),
        tools=[create_mcp_toolset()],
    )


root_agent = create_agent()

app = App(
    root_agent=root_agent,
    name="app",
)
