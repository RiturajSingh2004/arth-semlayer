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
"""Environment-driven configuration for the governed semantic layer."""

import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

DEFAULT_DEFINITIONS_DIR = Path(__file__).resolve().parent / "definitions"
DEFAULT_BQ_MCP_URL = "https://bigquery.googleapis.com/mcp"
PLACEHOLDER_PROJECTS = frozenset(
    {
        "",
        "YOUR_PROJECT_ID",
        "your-project-id",
        "<TODO: update-this-value>",
        "adk-verify-placeholder",
    }
)


def resolve_project_id(*candidates: str | None) -> str:
    """Return the first configured non-placeholder GCP project ID, or empty."""
    for item in candidates:
        if item is not None:
            cleaned = item.strip()
            if cleaned and cleaned not in PLACEHOLDER_PROJECTS:
                return cleaned
    return ""


class Config(BaseModel):
    """Runtime configuration for warehouse and registry resolution."""

    warehouse: Literal["duckdb", "bigquery"] = "duckdb"
    db_path: str = ""
    registry_dir: str = str(DEFAULT_DEFINITIONS_DIR)
    state_dir: str = ""
    bq_project: str = ""
    bq_dataset: str = "jaffle_shop"
    bq_mcp_url: str = DEFAULT_BQ_MCP_URL
    bq_service_account: str | None = None
    bq_max_bytes: int = 100_000_000

    @classmethod
    def from_env(cls) -> "Config":
        """Build configuration from environment variables."""
        wh_raw = (os.getenv("SEMLAYER_WAREHOUSE") or "duckdb").strip().lower()
        if wh_raw not in ("duckdb", "bigquery"):
            raise ValueError(
                f"invalid SEMLAYER_WAREHOUSE={wh_raw!r}; expected 'duckdb' or 'bigquery'"
            )
        reg_dir = (os.getenv("SEMLAYER_REGISTRY_DIR") or "").strip() or str(
            DEFAULT_DEFINITIONS_DIR
        )
        state_dir = (os.getenv("SEMLAYER_STATE_DIR") or "").strip()
        db_path = (os.getenv("SEMLAYER_DUCKDB_PATH") or "").strip()
        if not db_path:
            db_path = str(
                Path(tempfile.gettempdir())
                / "certified-bigquery-mcp"
                / "jaffle.duckdb"
            )
        bq_project = resolve_project_id(
            os.getenv("SEMLAYER_BQ_PROJECT"),
            os.getenv("GOOGLE_CLOUD_PROJECT"),
        )
        bq_dataset = (os.getenv("SEMLAYER_BQ_DATASET") or "jaffle_shop").strip()
        bq_mcp_url = (
            os.getenv("SEMLAYER_BQ_MCP_URL") or DEFAULT_BQ_MCP_URL
        ).strip()
        bq_sa = (os.getenv("SEMLAYER_BQ_SERVICE_ACCOUNT") or "").strip() or None
        max_bytes_raw = (
            os.getenv("SEMLAYER_BQ_MAX_BYTES") or "100000000"
        ).strip()
        return cls(
            warehouse=wh_raw,  # type: ignore[arg-type]
            db_path=db_path,
            registry_dir=reg_dir,
            state_dir=state_dir,
            bq_project=bq_project,
            bq_dataset=bq_dataset,
            bq_mcp_url=bq_mcp_url,
            bq_service_account=bq_sa,
            bq_max_bytes=int(max_bytes_raw),
        )
