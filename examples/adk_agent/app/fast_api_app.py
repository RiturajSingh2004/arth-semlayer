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

import logging
import os
import sys

import google.auth
from fastapi import FastAPI
from google.adk.cli.fast_api import get_fast_api_app
from google.cloud import logging as google_cloud_logging

from app.app_utils.telemetry import setup_telemetry
from app.app_utils.typing import Feedback
from app.semlayer.config import PLACEHOLDER_PROJECTS

setup_telemetry()
try:
    _, project_id = google.auth.default()
except Exception as exc:
    print(f"Warning: Google auth credentials not found: {exc}", file=sys.stderr)
    project_id = None

has_real_project = bool(project_id and project_id not in PLACEHOLDER_PROJECTS)
_cloud_logger = None
if has_real_project:
    try:
        _cloud_logging_client = google_cloud_logging.Client(project=project_id)
        _cloud_logger = _cloud_logging_client.logger(__name__)
    except Exception:
        _cloud_logger = None

allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",")
    if os.getenv("ALLOW_ORIGINS")
    else None
)

logs_bucket_name = os.environ.get("LOGS_BUCKET_NAME")

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
session_service_uri = None
artifact_service_uri = f"gs://{logs_bucket_name}" if logs_bucket_name else None

app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    artifact_service_uri=artifact_service_uri,
    allow_origins=allow_origins,
    session_service_uri=session_service_uri,
    otel_to_cloud=has_real_project,
)
app.title = "certified-bigquery-mcp"
app.description = (
    "Certification-gated semantic layer ADK agent over BigQuery MCP"
)


@app.post("/feedback")
def collect_feedback(feedback: Feedback) -> dict[str, str]:
    """Collect and log user feedback."""
    if _cloud_logger is not None:
        try:
            _cloud_logger.log_struct(feedback.model_dump(), severity="INFO")
        except Exception:
            logging.warning(
                "Cloud Logging unavailable; falling back to local logger."
            )
            logging.info(feedback.model_dump())
    else:
        logging.info(feedback.model_dump())
    return {"status": "success"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
