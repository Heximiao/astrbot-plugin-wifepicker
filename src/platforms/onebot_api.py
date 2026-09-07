from __future__ import annotations

from typing import Any, Mapping


def extract_message_id(resp: Any) -> Any:
    """Extract OneBot `message_id` from API response.

    Expected NapCat/OneBot response (common):
    {"status": "ok", "retcode": 0, "data": {"message_id": 123}}
    """

    if not isinstance(resp, Mapping):
        return None

    if "message_id" in resp:
        return resp.get("message_id")

    data = resp.get("data")
    if isinstance(data, Mapping) and "message_id" in data:
        return data.get("message_id")

    return None

