import logging
import shutil
import time
import wave
from pathlib import Path

from backend.src.voice_gateway.logging_config import log_stage_event
from backend.src.voice_gateway.models import TTSResult
from backend.src.voice_gateway.tts.base import TTSProvider

logger = logging.getLogger(__name__)


class FakeTTS(TTSProvider):
    """Deterministic TTS stand-in for tests.

    Copies the pre-prepared valid WAV to ``out_path`` and reads the sample
    rate from the WAV header. Performs no network activity, ignores the
    input text and never validates the WAV beyond header fields.

    Emits the same ``tts`` stage :func:`log_stage_event` on success that
    :class:`~backend.src.voice_gateway.tts.openai_compatible.OpenAICompatibleTTS`
    does, so full-turn tests driven through ``create_app()`` with this
    double injected can assert on the ``tts`` stage record exactly like the
    real provider (ТЗ §33).
    """

    def __init__(self, prepared_wav: Path) -> None:
        self._prepared_wav = prepared_wav

    def synthesize(
        self,
        text: str,
        out_path: Path,
        *,
        turn_id: str | None = None,
        device_id: str | None = None,
    ) -> TTSResult:
        stage_start = time.perf_counter()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._prepared_wav, out_path)
        with wave.open(str(out_path), "rb") as wav:
            sample_rate = wav.getframerate()
        log_stage_event(
            logger, "tts", turn_id=turn_id, device_id=device_id,
            duration_ms=int(round((time.perf_counter() - stage_start) * 1000)),
            status="success",
        )
        return TTSResult(wav_path=out_path, sample_rate=sample_rate)
