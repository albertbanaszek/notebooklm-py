"""Opt-in live deferred Studio creation, independent read-back, and owned cleanup."""

import os

import pytest

from notebooklm import NotebookLMClient
from notebooklm.options import AndroidBackendConfig, ClientConfig, WebBackendConfig
from notebooklm.types import ArtifactStatus, GenerationState

from .conftest import requires_auth

pytestmark = [
    requires_auth,
    pytest.mark.skipif(
        os.environ.get("NOTEBOOKLM_DEFERRED_STUDIO_CONFORMANCE") != "1",
        reason="manual conformance requires NOTEBOOKLM_DEFERRED_STUDIO_CONFORMANCE=1",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["web", "android"])
@pytest.mark.parametrize(
    "family", ["video", "report", "study_guide", "quiz", "flashcards", "infographic", "slide_deck"]
)
async def test_deferred_studio_is_scheduled_and_persisted(backend, family):
    config = ClientConfig(
        backend=AndroidBackendConfig() if backend == "android" else WebBackendConfig()
    )
    async with NotebookLMClient.from_storage(config=config) as client:
        notebook = await client.notebooks.create(f"E2E #2519 disposable {backend} {family}")
        try:
            source = await client.sources.add_text(
                notebook.id,
                "Deferred Studio test source",
                "Deferred generation queues notebook artifacts for a later time chosen by the server. "
                "Reports summarize source material. Quizzes and flashcards help learners recall it. "
                "Videos, infographics and slide decks explain it visually. "
                "This disposable notebook verifies persisted scheduling before execution.",
                wait=True,
            )
            result = await getattr(client.artifacts, f"generate_{family}")(
                notebook.id,
                source_ids=[source.id],
                later=True,
            )
            assert result.task_id
            assert result.status is GenerationState.SCHEDULED, result
            assert result.is_scheduled and not result.is_terminal
            artifact = await client.artifacts.get(notebook.id, result.task_id)
            assert artifact.status == ArtifactStatus.SCHEDULED
            assert artifact.is_scheduled
            polled = await client.artifacts.poll_status(notebook.id, result.task_id)
            assert polled.status is GenerationState.SCHEDULED
            await client.artifacts.delete(notebook.id, result.task_id)
            assert await client.artifacts.get_or_none(notebook.id, result.task_id) is None
        finally:
            await client.notebooks.delete(notebook.id)
