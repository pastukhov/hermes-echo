from enum import Enum


class ErrorCode(Enum):
    """
    Backend-wide typed enum all possible error codes voice turn.
    """
    AUDIO_RECEIVE_FAILED = "audio_receive_failed"
    AUDIO_INVALID = "audio_invalid"
    STT_FAILED = "stt_failed"
    HERMES_FAILED = "hermes_failed"
    HERMES_INVALID_RESPONSE = "hermes_invalid_response"
    NOTE_WRITE_FAILED = "note_write_failed"
    TTS_FAILED = "tts_failed"
    INTERNAL_ERROR = "internal_error"

    def __str__(self) -> str:
        return self.value