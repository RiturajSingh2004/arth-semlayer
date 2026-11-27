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
"""Runnability smoke tests for certified-bigquery-mcp."""

import ast
from pathlib import Path

import app
import app.agent
from fastapi import FastAPI
from google.adk.agents import Agent, LlmAgent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset


def test_adk_run_runnability() -> None:
    """Verify `app` exports `root_agent` and `app` wired with McpToolset."""
    assert hasattr(app, "root_agent") and hasattr(app, "app")
    assert app.agent.root_agent is not None
    assert isinstance(app.agent.root_agent, LlmAgent)
    assert isinstance(app.agent.root_agent, Agent)
    assert isinstance(app.agent.root_agent.model, Gemini)
    assert app.agent.root_agent.model.model
    assert any(
        isinstance(tool, McpToolset) for tool in app.agent.root_agent.tools
    )
    assert isinstance(app.agent.app, App)


def test_dotenv_loaded_in_package_init_not_agent() -> None:
    """Verify load_dotenv() lives in app/__init__.py and not in app/agent.py."""
    pkg_dir = Path(app.__file__).resolve().parent
    init_tree = ast.parse((pkg_dir / "__init__.py").read_text(encoding="utf-8"))
    agent_tree = ast.parse((pkg_dir / "agent.py").read_text(encoding="utf-8"))

    init_calls = {
        node.func.id
        for node in ast.walk(init_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    agent_calls = {
        node.func.id
        for node in ast.walk(agent_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "load_dotenv" in init_calls
    assert "load_dotenv" not in agent_calls


def test_web_server_runnability() -> None:
    """Verify FastAPI serving entry point imports cleanly without credentials."""
    import app.fast_api_app

    assert app.fast_api_app.app is not None
    assert isinstance(app.fast_api_app.app, FastAPI)
