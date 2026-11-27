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
"""Certification-gated semantic layer core for BigQuery MCP and DuckDB."""

from .bigquery import BigQueryMCP, parse_bq_payload, render_sql
from .compiler import Compiled, CompileError, SimpleCompiler
from .config import DEFAULT_DEFINITIONS_DIR, Config
from .graph import Graph
from .models import Edge, Entity, Evidence, Metric, Node, Rule, State, ident
from .policy import Decision, evaluate, unrestricted
from .registry import Registry
from .seed import build_db
from .service import Refusal, Service
from .warehouse import DuckDBWarehouse, QueryResult, make_warehouse

__all__ = [
    "DEFAULT_DEFINITIONS_DIR",
    "BigQueryMCP",
    "CompileError",
    "Compiled",
    "Config",
    "Decision",
    "DuckDBWarehouse",
    "Edge",
    "Entity",
    "Evidence",
    "Graph",
    "Metric",
    "Node",
    "QueryResult",
    "Refusal",
    "Registry",
    "Rule",
    "Service",
    "SimpleCompiler",
    "State",
    "build_db",
    "evaluate",
    "ident",
    "make_warehouse",
    "parse_bq_payload",
    "render_sql",
    "unrestricted",
]
