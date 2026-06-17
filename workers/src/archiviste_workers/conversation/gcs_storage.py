"""GCS Markdown append-only storage for conversations (ING-003)."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, cast

from google.api_core.exceptions import (
    GoogleAPICallError,
    NotFound,
    PreconditionFailed,
    RetryError,
    ServiceUnavailable,
)
from google.auth.credentials import AnonymousCredentials
from google.cloud import storage

from archiviste_workers.conversation.models import (
    ConcurrentWriteError,
    ConversationAlreadyExistsError,
    ConversationObjectMissingError,
    Role,
    StorageUnavailableError,
)

# AC-2 / AC-3: byte-for-byte format. Do not reformat without updating tests.
_HEADER_TEMPLATE = (
    "# Conversation {conversation_id}\n\nCreated: {created_at}\nUser: {user_id}\n\n---\n"
)
_BLOCK_TEMPLATE = "## [{timestamp}] {role}\n{content}\n\n"

# AC-4: deterministic backoff between retries (no jitter, spec H6).
_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (0.05, 0.20, 0.80)
_MARKDOWN_CONTENT_TYPE = "text/markdown; charset=utf-8"


def build_client(*, emulator_host: str | None) -> storage.Client:
    """Build a GCS client; emulator path skips ADC entirely."""
    if emulator_host:
        # AnonymousCredentials.__init__ is unannotated upstream; cast bypasses
        # mypy strict no-untyped-call without disabling lint globally.
        anon = cast("Any", AnonymousCredentials)()
        return storage.Client(
            credentials=anon,
            project="archiviste-test",
            client_options={"api_endpoint": emulator_host},
        )
    return storage.Client()


class GcsConversationStorage:
    """Thin sync-over-async wrapper around the GCS blob lifecycle."""

    def __init__(self, *, bucket_name: str, client: storage.Client) -> None:
        self._bucket_name = bucket_name
        self._client = client

    @property
    def bucket_name(self) -> str:
        return self._bucket_name

    def gcs_uri(self, conversation_id: str) -> str:
        return f"gs://{self._bucket_name}/{conversation_id}.md"

    async def create_conversation_object(
        self, *, conversation_id: str, user_id: str, created_at: datetime
    ) -> int:
        header = _HEADER_TEMPLATE.format(
            conversation_id=conversation_id,
            created_at=created_at.isoformat(),
            user_id=user_id,
        )
        return await asyncio.to_thread(self._upload_header, conversation_id, header)

    async def append_block(
        self, *, conversation_id: str, role: Role, content: str, timestamp: datetime
    ) -> tuple[int, int]:
        """Append one block. Returns (generation, new_byte_size)."""
        block = _BLOCK_TEMPLATE.format(
            timestamp=timestamp.isoformat(),
            role=role,
            content=content,
        )
        return await asyncio.to_thread(self._read_modify_write, conversation_id, block)

    def _upload_header(self, conversation_id: str, header: str) -> int:
        bucket = self._client.bucket(self._bucket_name)
        blob = bucket.blob(f"{conversation_id}.md")
        try:
            blob.upload_from_string(
                header, content_type=_MARKDOWN_CONTENT_TYPE, if_generation_match=0
            )
        except PreconditionFailed as exc:
            raise ConversationAlreadyExistsError from exc
        except (ServiceUnavailable, RetryError, GoogleAPICallError, OSError) as exc:
            raise StorageUnavailableError from exc
        blob.reload()
        return int(blob.generation)

    def _read_modify_write(self, conversation_id: str, block: str) -> tuple[int, int]:
        bucket = self._client.bucket(self._bucket_name)
        blob = bucket.blob(f"{conversation_id}.md")
        last_precondition: PreconditionFailed | None = None
        for attempt in range(len(_RETRY_BACKOFF_SECONDS) + 1):
            try:
                current = blob.download_as_bytes()
            except NotFound as exc:
                # Object row exists but the GCS object is absent (orphaned
                # conversation). Raise so the router can self-heal by
                # recreating it before retrying the append (Part B fix).
                raise ConversationObjectMissingError from exc
            except (ServiceUnavailable, RetryError, GoogleAPICallError, OSError) as exc:
                raise StorageUnavailableError from exc
            generation = int(blob.generation or 0)
            new_payload = current + block.encode("utf-8")
            try:
                blob.upload_from_string(
                    new_payload,
                    content_type=_MARKDOWN_CONTENT_TYPE,
                    if_generation_match=generation,
                )
            except PreconditionFailed as exc:
                last_precondition = exc
                if attempt < len(_RETRY_BACKOFF_SECONDS):
                    # Spec H6: deterministic backoff 50/200/800 ms, re-read each retry.
                    time.sleep(_RETRY_BACKOFF_SECONDS[attempt])
                    continue
                break
            except (ServiceUnavailable, RetryError, GoogleAPICallError, OSError) as exc:
                raise StorageUnavailableError from exc
            blob.reload()
            return int(blob.generation), len(new_payload)
        raise ConcurrentWriteError from last_precondition
