"""Caller-owned aiortc integration for Web Interactive Audio Overviews.

Install aiortc separately. Supply a live audio MediaStreamTrack, consume remote
tracks in on_track, and signal join_requested when the listener asks to speak.
The SDK itself does not depend on aiortc or own these media resources.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from notebooklm import AudioEvent, NotebookLMClient


async def interactive_audio(
    client: NotebookLMClient,
    notebook_id: str,
    artifact_id: str,
    microphone: Any,
    join_requested: asyncio.Event,
    stop: asyncio.Event,
    on_event: Callable[[AudioEvent], None],
    on_track: Callable[[Any], None],
) -> None:
    """Run until stop is set; callbacks must not block the event loop.

    Caller also owns microphone capture and playback cleanup. Decoder/signaling
    failure is surfaced to this coroutine and closes the peer connection.
    """
    from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription

    async with client.interactive_audio.connect(notebook_id, artifact_id) as session:
        pc = RTCPeerConnection(
            RTCConfiguration(
                iceServers=[
                    RTCIceServer(list(s.urls), username=s.username, credential=s.credential)
                    for s in session.ice_servers
                ]
            )
        )
        # aiortc has no public relay-only policy setting. Never silently ignore it.
        if session.ice_transport_policy != "all":
            await pc.close()
            raise RuntimeError("Use a WebRTC implementation supporting the requested ICE policy")
        failure = asyncio.get_running_loop().create_future()
        active: Any = None
        setup_complete = asyncio.Event()
        tasks: list[asyncio.Task[Any]] = []

        def fail(error: Exception) -> None:
            if not failure.done():
                failure.set_result(error)

        def receive(message: Any) -> None:
            try:
                if not isinstance(message, bytes):
                    raise ValueError("Expected a binary data-channel message")
                for event in session.receive(message):
                    on_event(event)
            except Exception as error:
                fail(error)

        @pc.on("datachannel")
        def channel_ready(channel: Any) -> None:
            nonlocal active
            active = channel
            channel.on("message", receive)

            def setup() -> None:
                try:
                    channel.send(session.setup_frame())
                    setup_complete.set()
                except Exception as error:
                    fail(error)

            if channel.readyState == "open":
                setup()
            else:
                channel.on("open", setup)

        @pc.on("connectionstatechange")
        def connection_state() -> None:
            if pc.connectionState in ("failed", "closed"):
                fail(RuntimeError("WebRTC connection ended"))

        async def join_loop() -> None:
            while True:
                await join_requested.wait()
                await setup_complete.wait()
                join_requested.clear()
                if active is not None and active.readyState == "open":
                    active.send(session.join_frame())

        pc.on("track", on_track)
        try:
            pc.addTrack(microphone)
            pc.createDataChannel("data-channel")
            await pc.setLocalDescription(await pc.createOffer())
            answer = await session.negotiate(pc.localDescription.sdp)
            await pc.setRemoteDescription(RTCSessionDescription(answer.sdp, answer.type))
            tasks = [asyncio.create_task(join_loop()), asyncio.create_task(stop.wait())]
            done, _ = await asyncio.wait([*tasks, failure], return_when=asyncio.FIRST_COMPLETED)
            if failure in done:
                raise failure.result()
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            failure.cancel()
            await pc.close()
