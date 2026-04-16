from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_TRANSCRIBE_BACKEND = "local"
DEFAULT_LOCAL_TRANSCRIBE_MODEL = "small"
DEFAULT_OPENAI_TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
DEFAULT_TRANSCRIBE_LANGUAGE = "zh"
DEFAULT_AUDIO_EXT = ".m4a"


class AudioTranscriptionError(RuntimeError):
    """Raised when a voice attachment cannot be transcribed."""


@dataclass(slots=True)
class AudioAttachment:
    download_url: str
    file_name: str = ""
    mime_type: str = ""
    duration_ms: int | None = None
    item_type: str = ""


def default_transcribe_cli_path() -> Path:
    env_value = os.environ.get("TRANSCRIBE_CLI")
    if env_value:
        return Path(env_value).expanduser()
    return Path.home() / ".codex" / "skills" / "transcribe" / "scripts" / "transcribe_diarize.py"


class WeixinAudioTranscriber:
    def __init__(
        self,
        *,
        cache_root: Path,
        backend: str = DEFAULT_TRANSCRIBE_BACKEND,
        transcribe_cli: Path | None = None,
        model: str | None = None,
        language: str = DEFAULT_TRANSCRIBE_LANGUAGE,
    ):
        self.cache_root = cache_root.expanduser()
        self.backend = (backend or DEFAULT_TRANSCRIBE_BACKEND).strip().lower()
        self.transcribe_cli = (transcribe_cli or default_transcribe_cli_path()).expanduser()
        self.model = model or self._default_model_for_backend(self.backend)
        self.language = language
        self._whisper_model = None

    def availability_error(self) -> str | None:
        if shutil.which(sys.executable) is None:
            return "Python runtime is unavailable for voice transcription."
        if self.backend == "openai":
            if not self.transcribe_cli.exists():
                return f"Transcription CLI not found at {self.transcribe_cli}."
            if not os.environ.get("OPENAI_API_KEY"):
                return "OPENAI_API_KEY is not configured on this host."
            return None
        if self.backend == "local":
            if shutil.which("ffmpeg") is None:
                return "ffmpeg is not installed on this host."
            try:
                import faster_whisper  # noqa: F401
            except ImportError:
                return "faster-whisper is not installed on this host."
            return None
        return f"Unsupported transcription backend: {self.backend}"

    def transcribe_attachments(
        self,
        attachments: list[AudioAttachment],
        *,
        message_id: int | None,
        downloader,
    ) -> list[str]:
        error = self.availability_error()
        if error:
            raise AudioTranscriptionError(error)

        transcripts: list[str] = []
        job_root = self.cache_root / (str(message_id) if message_id is not None else "unknown-message")
        job_root.mkdir(parents=True, exist_ok=True)

        for index, attachment in enumerate(attachments, start=1):
            audio_path = job_root / self._attachment_filename(attachment, index=index)
            downloader(attachment.download_url, audio_path)
            if self.backend == "local":
                transcript_path = job_root / f"{audio_path.stem}.local.transcript.txt"
                transcripts.append(self._run_local_transcription(audio_path, transcript_path))
            else:
                transcript_path = job_root / f"{audio_path.stem}.transcript.txt"
                transcripts.append(self._run_openai_transcription(audio_path, transcript_path))

        return [text for text in transcripts if text.strip()]

    def _attachment_filename(self, attachment: AudioAttachment, *, index: int) -> str:
        if attachment.file_name.strip():
            return attachment.file_name.strip()
        parsed = urlparse(attachment.download_url)
        candidate = Path(parsed.path).name.strip()
        if candidate:
            return candidate
        return f"voice-{index}{DEFAULT_AUDIO_EXT}"

    def _run_openai_transcription(self, audio_path: Path, transcript_path: Path) -> str:
        command = [
            sys.executable,
            str(self.transcribe_cli),
            str(audio_path),
            "--response-format",
            "text",
            "--model",
            self.model,
            "--language",
            self.language,
            "--out",
            str(transcript_path),
        ]
        process = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )
        if process.returncode != 0:
            detail = process.stderr.strip() or process.stdout.strip() or "unknown transcription failure"
            raise AudioTranscriptionError(detail)
        if not transcript_path.exists():
            raise AudioTranscriptionError("Transcription CLI completed without producing an output file.")
        return transcript_path.read_text(encoding="utf-8").strip()

    def _run_local_transcription(self, audio_path: Path, transcript_path: Path) -> str:
        normalized_path = transcript_path.with_suffix(".wav")
        self._normalize_audio(audio_path, normalized_path)
        model = self._load_local_model()
        segments, _info = model.transcribe(
            str(normalized_path),
            language=self.language,
            vad_filter=True,
            beam_size=5,
            condition_on_previous_text=False,
        )
        text = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
        if not text:
            raise AudioTranscriptionError("Local transcription completed but produced empty text.")
        transcript_path.write_text(text, encoding="utf-8")
        return text

    def _normalize_audio(self, source_path: Path, target_path: Path) -> None:
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            raise AudioTranscriptionError("ffmpeg is not installed on this host.")
        command = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(source_path),
            "-ar",
            "16000",
            "-ac",
            "1",
            str(target_path),
        ]
        process = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )
        if process.returncode != 0:
            detail = process.stderr.strip() or process.stdout.strip() or "ffmpeg conversion failed"
            raise AudioTranscriptionError(detail)
        if not target_path.exists():
            raise AudioTranscriptionError("ffmpeg conversion completed without producing normalized audio.")

    def _load_local_model(self):
        if self._whisper_model is not None:
            return self._whisper_model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise AudioTranscriptionError("faster-whisper is not installed on this host.") from exc
        self._whisper_model = WhisperModel(self.model, device="cpu", compute_type="int8")
        return self._whisper_model

    @staticmethod
    def _default_model_for_backend(backend: str) -> str:
        if backend == "openai":
            return DEFAULT_OPENAI_TRANSCRIBE_MODEL
        return DEFAULT_LOCAL_TRANSCRIBE_MODEL
