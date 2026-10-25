"""Web Interactive Audio Overview signaling with caller-owned WebRTC."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from ._interactive_audio_wire import Decoder, envelope, field, setup_frame
from .exceptions import ConfigurationError, DecodingError, UnsupportedOperationError
from .interactive_audio import AudioEvent, AudioSessionDescription, IceServer


def _ice_servers(config: dict[str, Any]) -> tuple[IceServer, ...]:
    try:
        servers = config["iceServers"]
        if not isinstance(servers, list) or not servers:
            raise ValueError
        result = []
        for server in servers:
            if not isinstance(server, dict):
                raise ValueError
            urls = server["urls"]
            if isinstance(urls, str):
                urls = [urls]
            if (
                not isinstance(urls, list)
                or not urls
                or not all(
                    isinstance(url, str) and url.startswith(("stun:", "stuns:", "turn:", "turns:"))
                    for url in urls
                )
            ):
                raise ValueError
            username, credential = server.get("username"), server.get("credential")
            if any(v is not None and not isinstance(v, str) for v in (username, credential)):
                raise ValueError
            result.append(IceServer(tuple(urls), username, credential))
        return tuple(result)
    except (KeyError, ValueError, TypeError):
        raise DecodingError("Invalid Interactive Audio Overview ICE configuration") from None


class InteractiveAudioSession:
    """Signaling and control session for one existing Audio Overview.

    Own the WebRTC peer connection in the same context and close it in ``finally``.
    Send ``setup_frame()`` on the server-created data channel when it opens, then
    ``join_frame()`` when the listener requests a turn. Pass received binary frames
    to ``receive()``. This class owns no media tracks, queues or background tasks.
    Closing it only retires SDK signaling and decoder state; the caller must close
    the peer connection to terminate the media session on the server.
    """

    def __init__(self, signaling: Any, artifact_id: str, config: dict[str, Any]) -> None:
        self.ice_servers = _ice_servers(config)
        policy = config.get("iceTransportPolicy", "all")
        if policy not in ("all", "relay"):
            raise DecodingError("Invalid ICE transport policy")
        self.ice_transport_policy: str = policy
        self._signaling = signaling
        self._artifact_id = artifact_id
        self._decoder = Decoder()
        self._closed = False
        self._negotiated = False
        self._negotiating = False
        self._setup = False
        self._message_id = 0

    def _check_open(self) -> None:
        if self._closed:
            raise ConfigurationError("Interactive audio session is closed")

    async def negotiate(self, offer_sdp: str) -> AudioSessionDescription:
        """Exchange one SDP offer without retrying or renegotiating implicitly.

        A failed or canceled attempt retires this SDK session because allocation
        may have succeeded remotely. Close the caller-owned peer connection too.
        """
        self._check_open()
        if self._negotiated or self._negotiating:
            raise ConfigurationError("Interactive audio SDP negotiation has already started")
        if not isinstance(offer_sdp, str) or not offer_sdp.strip():
            raise ValueError("offer_sdp must be a nonempty string")
        self._negotiating = True
        try:
            answer = await self._signaling.negotiate(offer_sdp)
            self._check_open()
            if answer.get("type") != "answer" or not isinstance(answer.get("sdp"), str):
                raise DecodingError("Invalid Interactive Audio Overview SDP answer")
            if not answer["sdp"].strip():
                raise DecodingError("Empty Interactive Audio Overview SDP answer")
            self._negotiated = True
            return AudioSessionDescription("answer", answer["sdp"])
        except BaseException:
            self.close()
            raise
        finally:
            self._negotiating = False

    def setup_frame(self) -> bytes:
        """Build the initial artifact-binding frame; send exactly once on open."""
        self._check_open()
        if not self._negotiated or self._setup:
            raise ConfigurationError("Setup requires a negotiated session and can be built once")
        self._setup = True
        frame = setup_frame(self._artifact_id, self._message_id)
        self._message_id += 1
        return frame

    def join_frame(self) -> bytes:
        """Build a listener Join request; caller sends it on the open data channel."""
        self._check_open()
        if not self._setup:
            raise ConfigurationError("Send the setup frame before requesting Join")
        frame = envelope(field(3, b"\x08\x05"), self._message_id)
        self._message_id += 1
        return frame

    def receive(self, frame: bytes) -> tuple[AudioEvent, ...]:
        """Decode a binary data-channel frame; partial chunks return no events.

        Process each frame serially in transport order. Malformed, oversized or
        over-capacity messages retire the session and raise ``DecodingError``.
        Unknown message kinds are ignored, without assigning guessed semantics.
        """
        self._check_open()
        try:
            return self._decoder.feed(frame)
        except DecodingError:
            self.close()
            raise

    def close(self) -> None:
        """Idempotently retire SDK state; caller closes its peer connection."""
        self._closed = True
        self._decoder.clear()


class InteractiveAudioAPI:
    """Artifact-bound Interactive Audio Overviews, currently qualified on Web."""

    def __init__(self, signaling: Any, artifacts: Any, *, backend: str) -> None:
        self._signaling = signaling
        self._artifacts = artifacts
        self._backend = backend

    @asynccontextmanager
    async def connect(
        self, notebook_id: str, artifact_id: str
    ) -> AsyncIterator[InteractiveAudioSession]:
        """Validate an existing Audio Overview and fetch parsed ICE configuration.

        No overview is generated. The caller must supply and clean up WebRTC.
        General notebook Live Voice is a separate protocol and is not selected
        implicitly. Unsupported backends fail before dispatching an RPC.
        """
        if self._backend != "web":
            raise UnsupportedOperationError("Interactive Audio Overviews currently require Web")
        if not notebook_id or not artifact_id:
            raise ValueError("notebook_id and artifact_id are required")
        artifacts = await self._artifacts.list(notebook_id)
        if not any(a.id == artifact_id and a.kind == "audio" and a.is_completed for a in artifacts):
            raise ConfigurationError("artifact_id must identify an Audio Overview in this notebook")
        config = await self._signaling.get_ice_config()
        if config.get("blockStatus") != "NOT_BLOCKED":
            raise ConfigurationError("Interactive Audio Overviews are unavailable for this account")
        session = InteractiveAudioSession(self._signaling, artifact_id, config)
        try:
            yield session
        finally:
            session.close()
