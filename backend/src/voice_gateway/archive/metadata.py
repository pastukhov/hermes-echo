"""Archive turn metadata model (ТЗ section 19: metadata.json).

One voice turn keeps a single ``metadata.json``. On success it records the
full turn summary (transcript, reply, note, status); on failure the metadata
is still saved with the failure status and a sanitized error string.
"""
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TurnMetadata(BaseModel):
    """Shape of ``<turn-id>/metadata.json`` (ТЗ section 19).

    All fields except ``turn_id`` are optional: partial metadata is written
    as a voice turn progresses, and a failed turn may record only
    ``turn_id`` + ``status`` + ``error``. ``extra="allow"`` preserves any
    field written by a newer gateway version on merge, so existing turn
    metadata is never dropped.
    """

    model_config = ConfigDict(extra="allow")

    turn_id: UUID
    device_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    audio_duration_ms: int | None = None
    input_bytes: int | None = None
    transcript: str | None = None
    reply: str | None = None
    note_created: bool | None = None
    note_path: str | None = None
    status: str | None = None
    error: str | None = None

    def error_code(self) -> str | None:
        """Return the recorded failure code, or ``None`` on success turns.

        The stable machine-readable status is one of the ``ErrorCode``
        values (e.g. ``stt_failed``); ``success`` means no error was
        recorded.
        """
        if self.status is None:
            return None
        status = self.status.value if hasattr(self.status, "value") else str(self.status)
        return None if status == "success" else status
