"""Live target separation, bearer fencing and sanitized terminal status mapping."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from notebooklm._android import live_transport
from notebooklm.exceptions import AuthError, RateLimitError, ServerError


@pytest.mark.asyncio
async def test_dedicated_channel_and_bearer_cleanup(monkeypatch):
    eof = object()
    call = SimpleNamespace(read=AsyncMock(return_value=eof), write=AsyncMock(), cancel=Mock())
    invoke = Mock(return_value=call)
    channel = SimpleNamespace(stream_stream=Mock(return_value=invoke), close=AsyncMock())
    secure_channel = Mock(return_value=channel)
    grpc = SimpleNamespace(
        aio=SimpleNamespace(secure_channel=secure_channel, EOF=eof),
        ssl_channel_credentials=Mock(return_value="tls"),
    )
    monkeypatch.setattr(live_transport, "_default_grpc_loader", lambda: grpc)
    provider = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(token="secret", generation=4))
    )
    transport = live_transport.LiveTransport(SimpleNamespace(bearer_provider=provider), 7, 1024)
    await transport.open(asyncio.get_running_loop().time() + 10)
    provider.get.assert_awaited_once_with(7)
    assert secure_channel.call_args.args == ("labstailwind.pa.googleapis.com:443", "tls")
    assert secure_channel.call_args.kwargs["options"] == (
        ("grpc.max_receive_message_length", 1024),
    )
    assert channel.stream_stream.call_args.args == (live_transport.METHOD,)
    assert invoke.call_args.kwargs["metadata"] == (("authorization", "Bearer secret"),)
    assert 0 < invoke.call_args.kwargs["timeout"] <= 10
    await transport.write(b"audio")
    call.write.assert_awaited_once_with(b"audio")
    assert await transport.read() is None
    call.read.return_value = b"response"
    assert await transport.read() == b"response"
    await transport.close()
    await transport.close()
    channel.close.assert_awaited_once()
    assert transport.call is None and transport._credential is None


@pytest.mark.asyncio
async def test_close_during_bearer_mint_cannot_publish_channel(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()

    async def get(epoch):
        started.set()
        await release.wait()
        return SimpleNamespace(token="secret", generation=1)

    monkeypatch.setattr(
        live_transport, "_default_grpc_loader", lambda: pytest.fail("channel created")
    )
    transport = live_transport.LiveTransport(
        SimpleNamespace(bearer_provider=SimpleNamespace(get=get)), 1, 100
    )
    task = asyncio.create_task(transport.open(None))
    await started.wait()
    await transport.close()
    release.set()
    await task
    assert transport.call is None


@pytest.mark.parametrize(
    "status,expected",
    [
        ("RESOURCE_EXHAUSTED", RateLimitError),
        ("UNAUTHENTICATED", AuthError),
        ("INTERNAL", ServerError),
    ],
)
def test_sanitized_status_mapping_and_generation_invalidation(status, expected):
    grpc = pytest.importorskip("grpc")

    class Error(Exception):
        def code(self):
            return getattr(grpc.StatusCode, status)

        def details(self):
            return "secret bearer and transcript"

    provider = SimpleNamespace(invalidate=Mock())
    transport = live_transport.LiveTransport(SimpleNamespace(bearer_provider=provider), 1, 100)
    transport._credential = SimpleNamespace(generation=9)
    error = transport.map_error(Error("secret"))
    assert isinstance(error, expected)
    assert "secret" not in repr(error)
    assert error.__context__ is None
    if status == "UNAUTHENTICATED":
        provider.invalidate.assert_called_once_with(9)
    else:
        provider.invalidate.assert_not_called()
