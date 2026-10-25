"""Caller-owned WebRTC example cleanup without installing a media stack."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from notebooklm._interactive_audio import InteractiveAudioAPI
from notebooklm._web.interactive_audio import WebInteractiveAudioSignaling
from notebooklm.exceptions import DecodingError
from notebooklm.rpc import RPCMethod


@pytest.fixture
def example_harness(monkeypatch):
    peers = []
    connected = asyncio.Event()
    scenario = SimpleNamespace(malformed=False, negotiation_failure=False, delayed_open=False)

    class Channel:
        readyState = "open"

        def __init__(self):
            self.callbacks = {}
            self.sent = []

        def on(self, name, callback):
            self.callbacks[name] = callback

        def send(self, frame):
            self.sent.append(frame)

    class Peer:
        connectionState = "new"

        def __init__(self, config):
            self.callbacks = {}
            self.closed = False
            self.channel = Channel()
            if scenario.delayed_open:
                self.channel.readyState = "connecting"
            peers.append(self)

        def on(self, name, callback=None):
            def register(fn):
                self.callbacks[name] = fn
                return fn

            return register(callback) if callback is not None else register

        def addTrack(self, track):
            pass

        def createDataChannel(self, label):
            return Channel()

        async def createOffer(self):
            return SimpleNamespace(sdp="offer", type="offer")

        async def setLocalDescription(self, description):
            self.localDescription = description

        async def setRemoteDescription(self, description):
            self.callbacks["datachannel"](self.channel)
            if scenario.malformed:
                self.channel.callbacks["message"](b"\x80")
            connected.set()

        async def close(self):
            self.closed = True
            self.connectionState = "closed"
            self.callbacks["connectionstatechange"]()

    monkeypatch.setitem(
        sys.modules,
        "aiortc",
        SimpleNamespace(
            RTCConfiguration=lambda **kw: kw,
            RTCIceServer=lambda *args, **kw: kw,
            RTCPeerConnection=Peer,
            RTCSessionDescription=lambda sdp, type: SimpleNamespace(sdp=sdp, type=type),
        ),
    )

    async def signal(method, params, **kwargs):
        if method is RPCMethod.GET_ICE_CONFIG:
            return [
                json.dumps(
                    {"blockStatus": "NOT_BLOCKED", "iceServers": [{"urls": "stun:example.test"}]}
                )
            ]
        if scenario.negotiation_failure:
            raise RuntimeError("negotiation failed")
        return [json.dumps({"type": "answer", "sdp": "answer"})]

    raw = SimpleNamespace(call=AsyncMock(side_effect=signal))
    artifacts = SimpleNamespace(
        list=AsyncMock(
            return_value=[SimpleNamespace(id="artifact", kind="audio", is_completed=True)]
        )
    )
    client = SimpleNamespace(
        interactive_audio=InteractiveAudioAPI(
            WebInteractiveAudioSignaling(raw), artifacts, backend="web"
        )
    )
    path = Path(__file__).resolve().parents[2] / "examples/interactive_audio.py"
    spec = importlib.util.spec_from_file_location("interactive_audio_example_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    join, stop = asyncio.Event(), asyncio.Event()

    async def run():
        await module.interactive_audio(
            client, "nb", "artifact", object(), join, stop, lambda event: None, lambda track: None
        )

    return SimpleNamespace(
        run=run, peers=peers, connected=connected, scenario=scenario, join=join, stop=stop
    )


@pytest.mark.asyncio
async def test_example_stop_closes_peer(example_harness):
    h = example_harness
    h.stop.set()
    await asyncio.wait_for(h.run(), 1)
    assert h.peers[0].closed
    assert len(h.peers[0].channel.sent) == 1  # artifact setup


@pytest.mark.asyncio
async def test_example_negotiation_failure_closes_peer(example_harness):
    h = example_harness
    h.scenario.negotiation_failure = True
    with pytest.raises(RuntimeError, match="negotiation failed"):
        await asyncio.wait_for(h.run(), 1)
    assert h.peers[0].closed


@pytest.mark.asyncio
async def test_example_decoder_failure_closes_peer(example_harness):
    h = example_harness
    h.scenario.malformed = True
    with pytest.raises(DecodingError):
        await asyncio.wait_for(h.run(), 1)
    assert h.peers[0].closed


@pytest.mark.asyncio
async def test_example_cancellation_closes_peer_and_joins_tasks(example_harness):
    h = example_harness
    task = asyncio.create_task(h.run())
    await asyncio.wait_for(h.connected.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert h.peers[0].closed


@pytest.mark.asyncio
async def test_example_retains_join_until_channel_setup(example_harness):
    h = example_harness
    h.scenario.delayed_open = True
    h.join.set()
    task = asyncio.create_task(h.run())
    try:
        await asyncio.wait_for(h.connected.wait(), 1)
        await asyncio.sleep(0)  # let Join wait for channel setup
        assert h.join.is_set()
        channel = h.peers[0].channel
        assert not channel.sent
        channel.readyState = "open"
        channel.callbacks["open"]()
        await asyncio.sleep(0)
        assert len(channel.sent) == 2  # setup, then exactly one Join
        assert not h.join.is_set()
        h.stop.set()
        await asyncio.wait_for(task, 1)
        assert h.peers[0].closed
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
