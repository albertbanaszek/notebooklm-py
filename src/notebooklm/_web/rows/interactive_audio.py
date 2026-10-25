"""Decode the Web signaling JSON-in-string response envelope."""

import json
from typing import Any

from ...exceptions import DecodingError


def decode_signaling_response(response: Any) -> dict[str, Any]:
    try:
        if not isinstance(response, list) or not response or not isinstance(response[0], str):
            raise ValueError
        value = json.loads(response[0])
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise DecodingError("Invalid Interactive Audio Overview signaling response") from None
