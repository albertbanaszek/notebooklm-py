"""Offline synthetic contract replay for Interactive Audio signaling.

The cassette is hand-built from the qualified response shapes, not a recording
or fresh server evidence. It verifies real HTTP encoding/decoding and the public
session's SDP exchange without credentials, media or a network connection.
"""

import json
from urllib.parse import parse_qs

import pytest

from notebooklm import InteractiveAudioSession, NotebookLMClient
from notebooklm._web.interactive_audio import WebInteractiveAudioSignaling
from notebooklm.rpc import RPCMethod
from tests.vcr_config import notebooklm_vcr

pytestmark = pytest.mark.vcr


# Explicit replay-only even when the broader suite enables VCR recording.
@notebooklm_vcr.use_cassette(
    "interactive_audio_signaling_synthetic_contract.yaml", record_mode="none", inject_cassette=True
)
@pytest.mark.asyncio
async def test_synthetic_interactive_audio_signaling_contract(cassette=None, *, auth_tokens):
    # VCR injects this optional parameter; pytest supplies only auth_tokens.
    assert cassette is not None
    async with NotebookLMClient(auth_tokens) as client:
        signaling = WebInteractiveAudioSignaling(client.raw)
        ice = await signaling.get_ice_config()
        session = InteractiveAudioSession(signaling, "fixture-artifact", ice)
        try:
            assert session.ice_servers[0].urls == ("stun:fixture.example.test:19302",)
            assert session.ice_transport_policy == "all"
            answer = await session.negotiate("v=0\r\ns=synthetic-offer\r\n")
            assert answer.type == "answer"
            assert answer.sdp == "v=0\r\ns=synthetic-answer\r\n"
            assert session.setup_frame()
            assert session.join_frame()
        finally:
            session.close()
    assert cassette.all_played
    assert cassette.play_count == 2
    for request, method, expected in zip(
        cassette.requests,
        (RPCMethod.GET_ICE_CONFIG, RPCMethod.SEND_SDP_OFFER),
        ([], [{"type": "offer", "sdp": "v=0\r\ns=synthetic-offer\r\n"}]),
        strict=True,
    ):
        body = request.body.decode() if isinstance(request.body, bytes) else request.body
        envelope = json.loads(parse_qs(body)["f.req"][0])[0][0]
        assert envelope[0] == method.value
        params = json.loads(envelope[1])
        assert (json.loads(params[0]) if params else []) == (expected[0] if expected else [])
