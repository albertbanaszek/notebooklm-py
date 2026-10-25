"""Interactive Audio Overview signaling, lifecycle and bounded protocol decoding."""

import asyncio
import gzip
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from notebooklm._interactive_audio import InteractiveAudioAPI
from notebooklm._interactive_audio_wire import Decoder, envelope, field, fields, varint
from notebooklm._web.interactive_audio import WebInteractiveAudioSignaling
from notebooklm.exceptions import ConfigurationError, DecodingError, UnsupportedOperationError
from notebooklm.interactive_audio import AudioAgentError, AudioControl, AudioTranscript
from notebooklm.rpc import RPCMethod


@pytest.fixture
def api():
    raw = SimpleNamespace(
        call=AsyncMock(
            return_value=[
                json.dumps(
                    {
                        "blockStatus": "NOT_BLOCKED",
                        "iceServers": [
                            {
                                "urls": ["turn:example.test"],
                                "username": "private-user",
                                "credential": "private-secret",
                            }
                        ],
                    }
                )
            ]
        )
    )
    artifacts = SimpleNamespace(
        list=AsyncMock(
            return_value=[SimpleNamespace(id="artifact", kind="audio", is_completed=True)]
        )
    )
    return InteractiveAudioAPI(WebInteractiveAudioSignaling(raw), artifacts, backend="web")


@pytest.mark.asyncio
async def test_artifact_binding_and_signaling(api):
    async with api.connect("notebook", "artifact") as session:
        assert session.ice_servers[0].urls == ("turn:example.test",)
        assert "private-secret" not in repr(session.ice_servers)
        assert "private-user" not in repr(session.ice_servers)
        api._signaling._raw.call.assert_awaited_once_with(
            RPCMethod.GET_ICE_CONFIG, [], disable_internal_retries=True
        )
        api._signaling._raw.call.return_value = [
            json.dumps({"type": "answer", "sdp": "private-sdp"})
        ]
        answer = await session.negotiate("offer")
        assert answer.sdp == "private-sdp"
        assert "private-sdp" not in repr(answer)
        call = api._signaling._raw.call.call_args
        assert call.args == (RPCMethod.SEND_SDP_OFFER, ['{"type":"offer","sdp":"offer"}'])
        assert call.kwargs["disable_internal_retries"] is True
        frame = session.setup_frame()
        payload = fields(fields(frame)[2])[4]
        setup = fields(fields(fields(payload)[1])[1])
        config = fields(fields(setup[2])[2])
        assert config[1] == b"artifact"
        assert config[4] == 1
        assert fields(fields(session.join_frame())[2])[1] == 1
        with pytest.raises(ConfigurationError):
            session.setup_frame()
        with pytest.raises(ConfigurationError):
            await session.negotiate("again")
    with pytest.raises(ConfigurationError):
        session.join_frame()
    session.close()


@pytest.mark.asyncio
async def test_unsupported_backend_fails_without_rpc(api):
    api._backend = "android"
    with pytest.raises(UnsupportedOperationError):
        async with api.connect("nb", "artifact"):
            pytest.fail("entered")
    api._signaling._raw.call.assert_not_awaited()
    api._artifacts.list.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_artifact_fails_before_signaling(api):
    with pytest.raises(ConfigurationError):
        async with api.connect("nb", "wrong"):
            pytest.fail("entered")
    api._signaling._raw.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_body_cancellation_retires_session(api):
    with pytest.raises(asyncio.CancelledError):
        async with api.connect("nb", "artifact") as session:
            raise asyncio.CancelledError
    with pytest.raises(ConfigurationError):
        session.receive(b"")


@pytest.mark.asyncio
async def test_failed_negotiation_is_not_replayed(api):
    async with api.connect("nb", "artifact") as session:
        api._signaling._raw.call.side_effect = asyncio.CancelledError
        with pytest.raises(asyncio.CancelledError):
            await session.negotiate("offer")
        with pytest.raises(ConfigurationError):
            await session.negotiate("offer")
        assert api._signaling._raw.call.await_count == 2  # one ICE + one offer


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [None, [], ["secret"], ['{"iceServers":[]}']])
async def test_invalid_signaling_is_sanitized(api, response):
    api._signaling._raw.call.return_value = response
    with pytest.raises((DecodingError, ConfigurationError)) as error:
        async with api.connect("nb", "artifact"):
            pytest.fail("entered")
    assert "secret" not in str(error.value)


def transcript(text="hello", role=1):
    return field(role, field(3, text) + b"\x20\x01" + field(5, "item"))


def fragment(ident, total, offset, part):
    return field(
        2,
        b"\x08"
        + varint(ident)
        + b"\x10"
        + varint(total)
        + b"\x18"
        + varint(offset)
        + field(4, part),
    )


def test_chunk_reassembly_and_typed_events():
    decoder = Decoder()
    payload = transcript() + field(7, b"\x08\x04") + field(9, b"")
    assert decoder.feed(fragment(7, len(payload), 0, payload[:5])) == ()
    events = decoder.feed(fragment(7, len(payload), 5, payload[5:]))
    assert events == (
        AudioTranscript("user", "hello", "item", True),
        AudioControl("microphone", 4),
        AudioAgentError(),
    )
    assert not decoder.chunks


def test_control_fields_are_not_guessed_as_numeric_types():
    payload = field(6, field(1, "playback-id")) + field(8, field(1, "status"))
    assert Decoder().feed(field(1, payload)) == (
        AudioControl("playback", None),
        AudioControl("status", None),
    )


def test_compression_and_unknown_messages():
    decoder = Decoder()
    assert decoder.feed(field(1, gzip.compress(transcript(role=2), mtime=0)) + b"\x18\x01") == (
        AudioTranscript("model", "hello", "item", True),
    )
    assert decoder.feed(envelope(field(99, b"unknown"), 0)) == ()


@pytest.mark.parametrize(
    "frame",
    [
        b"\x80",
        b"\x00",
        b"\x0a\x05x",
        b"\x0b",
        b"\x08\x01",
        field(1, b"x") + b"\x18\x02",
        fragment(1, 8, 1, b"x"),
        fragment(1, 1, 0, b"too long"),
        field(1, gzip.compress(b"x" * 10000, mtime=0)) + b"\x18\x01",
    ],
)
def test_invalid_or_oversized_frames_are_bounded(frame):
    decoder = Decoder(max_bytes=1024)
    with pytest.raises(DecodingError):
        decoder.feed(frame)
    assert not decoder.chunks


def test_total_fragment_reservations_are_bounded():
    decoder = Decoder(max_bytes=100)
    assert decoder.feed(fragment(1, 60, 0, b"a")) == ()
    with pytest.raises(DecodingError):
        decoder.feed(fragment(2, 60, 0, b"b"))
    assert not decoder.chunks


def test_duplicate_or_out_of_order_fragment_fails():
    decoder = Decoder()
    frame = fragment(1, 10, 0, b"a")
    decoder.feed(frame)
    with pytest.raises(DecodingError):
        decoder.feed(frame)


@pytest.mark.asyncio
async def test_decode_failure_retires_session(api):
    async with api.connect("nb", "artifact") as session:
        with pytest.raises(ConfigurationError):
            session.setup_frame()
        with pytest.raises(ConfigurationError):
            session.join_frame()
        with pytest.raises(DecodingError):
            session.receive(b"\x80")
        with pytest.raises(ConfigurationError):
            session.receive(b"")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer", [{"type": "offer", "sdp": "secret"}, {"type": "answer", "sdp": ""}]
)
async def test_invalid_sdp_answer_retires_session(api, answer):
    async with api.connect("nb", "artifact") as session:
        with pytest.raises(ValueError):
            await session.negotiate("")
        api._signaling._raw.call.return_value = [json.dumps(answer)]
        with pytest.raises(DecodingError) as error:
            await session.negotiate("offer")
        assert "secret" not in str(error.value)
        with pytest.raises(ConfigurationError):
            await session.negotiate("offer")


@pytest.mark.asyncio
async def test_concurrent_negotiation_and_close_fence(api):
    async with api.connect("nb", "artifact") as session:
        entered, release = asyncio.Event(), asyncio.Event()

        async def respond(*args, **kwargs):
            entered.set()
            await release.wait()
            return [json.dumps({"type": "answer", "sdp": "private"})]

        api._signaling._raw.call.side_effect = respond
        task = asyncio.create_task(session.negotiate("offer"))
        await entered.wait()
        with pytest.raises(ConfigurationError):
            await session.negotiate("second")
        session.close()
        release.set()
        with pytest.raises(ConfigurationError):
            await task


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config",
    [
        {"iceServers": []},
        {"iceServers": [None]},
        {"iceServers": [{"urls": ["https://secret"]}]},
        {"iceServers": [{"urls": "stun:example.test", "credential": 123}]},
        {"iceServers": [{"urls": "stun:example.test"}], "iceTransportPolicy": "secret"},
    ],
)
async def test_invalid_ice_configuration(api, config):
    config["blockStatus"] = "NOT_BLOCKED"
    api._signaling._raw.call.return_value = [json.dumps(config)]
    with pytest.raises(DecodingError) as error:
        async with api.connect("nb", "artifact"):
            pytest.fail("entered")
    assert "secret" not in str(error.value)


@pytest.mark.asyncio
async def test_missing_ids_and_unfinished_artifact(api):
    with pytest.raises(ValueError):
        async with api.connect("", "artifact"):
            pytest.fail("entered")
    api._artifacts.list.return_value[0].is_completed = False
    with pytest.raises(ConfigurationError):
        async with api.connect("nb", "artifact"):
            pytest.fail("entered")
    api._signaling._raw.call.assert_not_awaited()


@pytest.mark.parametrize(
    "frame",
    [
        b"x" * 1025,
        b"\x08" + b"\x80" * 10,
        field(1, b"") + field(2, b""),
        field(1, b"abc") + b"\x18\x01",
        field(1, b"\x08\x01"),  # utterance must be length-delimited
        envelope(field(1, b"\x18\x01"), 0),  # text must be bytes
        envelope(field(7, field(1, b"bad-type")), 0),
        field(2, b"\x08\x01\x10\x01\x18\x00"),  # empty fragment
    ],
)
def test_decoder_rejects_malformed_types_and_frame_limits(frame):
    with pytest.raises(DecodingError):
        Decoder(max_bytes=1024).feed(frame)
