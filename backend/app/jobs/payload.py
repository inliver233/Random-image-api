from __future__ import annotations

import json
from typing import Any

from app.jobs.errors import JobPermanentError


def parse_job_payload_object(payload_json: str) -> dict[str, Any]:
    """Parse job payload_json as a JSON object; permanent-fail otherwise."""
    try:
        data = json.loads(payload_json)
    except Exception as exc:
        raise JobPermanentError("payload_json is not valid JSON") from exc
    if not isinstance(data, dict):
        raise JobPermanentError("payload_json must be an object")
    return data
