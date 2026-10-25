"""Web-owned ICE/SDP dispatch for artifact-bound Interactive Audio Overviews."""

from __future__ import annotations

import json
from typing import Any

from ..rpc import RPCMethod
from .rows.interactive_audio import decode_signaling_response


class WebInteractiveAudioSignaling:
    def __init__(self, raw: Any) -> None:
        self._raw = raw

    async def get_ice_config(self) -> dict[str, Any]:
        response = await self._raw.call(RPCMethod.GET_ICE_CONFIG, [], disable_internal_retries=True)
        return decode_signaling_response(response)

    async def negotiate(self, offer_sdp: str) -> dict[str, Any]:
        response = await self._raw.call(
            RPCMethod.SEND_SDP_OFFER,
            [json.dumps({"type": "offer", "sdp": offer_sdp}, separators=(",", ":"))],
            disable_internal_retries=True,
            read_timeout=30,
        )
        return decode_signaling_response(response)
