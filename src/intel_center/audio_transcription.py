from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
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
        transcribe_cli: Path | None = None,
        model: str = DEFAULT_TRANSCRIBE_MODEL,
        language: str = DEFAULT_TRANSCRIBE_LANGUAGE,
    ):
        self.cache_root = cache_root.expanduser()
        self.transcribe_cli = (transcribe_cli or default_transcribe_cli_path()).expanduser()
        self.model = model
        self.language = language

    def availability_error(self) -> str | None:
        if not self.transcribe_cli.exists():
            return f"Transcription CLI not found at {self.transcribe_cli}."
        if shutil.which(sys.executable) is None:
            return "Python runtime is unavailable for voice transcription."
        if not os.environ.get("OPENAI_API_KEY"):
            return "OPENAI_API_KEY is not configured on this host."
        return None

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
            transcript_path = job_root / f"{audio_path.stem}.transcript.txt"
            downloader(attachment.download_url, audio_path)
            transcripts.append(self._run_transcription(audio_path, transcript_path))

        return [text for text in transcripts if text.strip()]

    def _attachment_filename(self, attachment: AudioAttachment, *, index: int) -> str:
        if attachment.file_name.strip():
            return attachment.file_name.strip()
        parsed = urlparse(attachment.download_url)
        candidate = Path(parsed.path).name.strip()
        if candidate:
            return candidate
        return f"voice-{index}{DEFAULT_AUDIO_EXT}"

    def _run_transcription(self, audio_path: Path, transcript_path: Path) -> str:
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
