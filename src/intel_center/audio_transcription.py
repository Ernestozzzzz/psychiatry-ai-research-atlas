from __future__ import annotations

import os
import shutil
import subprocess
import sys
from base64 import b64decode
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


DEFAULT_TRANSCRIBE_BACKEND = "local"
DEFAULT_LOCAL_TRANSCRIBE_MODEL = "small"
DEFAULT_OPENAI_TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
DEFAULT_TRANSCRIBE_LANGUAGE = "zh"
DEFAULT_AUDIO_EXT = ".m4a"
DEFAULT_WECHAT_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
DEFAULT_FFMPEG_CANDIDATES = (
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/usr/bin/ffmpeg",
)
SILK_MAGIC = b"#!SILK_V3"


class AudioTranscriptionError(RuntimeError):
    """Raised when a voice attachment cannot be transcribed."""


@dataclass(slots=True)
class AudioAttachment:
    download_url: str
    file_name: str = ""
    mime_type: str = ""
    duration_ms: int | None = None
    item_type: str = ""
    encrypt_query_param: str = ""
    full_url: str = ""
    aes_key: str = ""
    encode_type: int | None = None


def default_transcribe_cli_path() -> Path:
    env_value = os.environ.get("TRANSCRIBE_CLI")
    if env_value:
        return Path(env_value).expanduser()
    return Path.home() / ".codex" / "skills" / "transcribe" / "scripts" / "transcribe_diarize.py"


def resolve_ffmpeg_bin() -> str | None:
    env_value = os.environ.get("FFMPEG_BIN")
    if env_value:
        candidate = Path(env_value).expanduser()
        if candidate.exists():
            return str(candidate)
    discovered = shutil.which("ffmpeg")
    if discovered:
        return discovered
    for candidate in DEFAULT_FFMPEG_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def build_cdn_download_url(encrypted_query_param: str, cdn_base_url: str = DEFAULT_WECHAT_CDN_BASE_URL) -> str:
    from urllib.parse import quote

    return f"{cdn_base_url.rstrip('/')}/download?encrypted_query_param={quote(encrypted_query_param)}"


def parse_aes_key(aes_key_value: str) -> bytes:
    decoded = b64decode(aes_key_value)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        as_ascii = decoded.decode("ascii", errors="ignore")
        if len(as_ascii) == 32 and all(char in "0123456789abcdefABCDEF" for char in as_ascii):
            return bytes.fromhex(as_ascii)
    raise AudioTranscriptionError("Unsupported WeChat media AES key format.")


def decrypt_aes_ecb(ciphertext: bytes, key: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


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
            if resolve_ffmpeg_bin() is None:
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
            downloader(attachment, audio_path)
            if self.backend == "local":
                transcript_path = job_root / f"{audio_path.stem}.local.transcript.txt"
                transcripts.append(self._run_local_transcription(audio_path, transcript_path, attachment))
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

    def _run_local_transcription(self, audio_path: Path, transcript_path: Path, attachment: AudioAttachment) -> str:
        normalized_path = self._prepare_local_audio(audio_path, transcript_path, attachment)
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

    def _prepare_local_audio(self, audio_path: Path, transcript_path: Path, attachment: AudioAttachment) -> Path:
        source_path = audio_path
        if attachment.aes_key.strip():
            decrypted = decrypt_aes_ecb(audio_path.read_bytes(), parse_aes_key(attachment.aes_key.strip()))
            if self._looks_like_silk(attachment, decrypted):
                wav_bytes = self._decode_silk_to_wav(decrypted)
                if wav_bytes is None:
                    raise AudioTranscriptionError("WeChat voice payload was decoded, but Silk transcoding failed.")
                wav_path = transcript_path.with_suffix(".wav")
                wav_path.write_bytes(wav_bytes)
                return wav_path
            source_path = transcript_path.with_suffix(self._preferred_suffix(attachment, decrypted))
            source_path.write_bytes(decrypted)

        normalized_path = transcript_path.with_suffix(".wav")
        self._normalize_audio(source_path, normalized_path)
        return normalized_path

    def _decode_silk_to_wav(self, silk_bytes: bytes) -> bytes | None:
        try:
            import io
            import wave
            import pysilk
        except ImportError as exc:
            raise AudioTranscriptionError("pysilk is not installed on this host.") from exc

        try:
            silk_stream = io.BytesIO(silk_bytes)
            pcm_stream = io.BytesIO()
            sample_rate = 24_000
            pysilk.decode(silk_stream, pcm_stream, sample_rate)
            pcm_bytes = pcm_stream.getvalue()
            output = io.BytesIO()
            with wave.open(output, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(pcm_bytes)
            return output.getvalue()
        except Exception:
            return None

    @staticmethod
    def _looks_like_silk(attachment: AudioAttachment, payload: bytes) -> bool:
        if payload.startswith(SILK_MAGIC):
            return True
        if attachment.mime_type.lower() == "audio/silk":
            return True
        return attachment.encode_type == 6

    @staticmethod
    def _preferred_suffix(attachment: AudioAttachment, payload: bytes) -> str:
        if payload.startswith(b"RIFF"):
            return ".wav"
        if payload.startswith(b"ID3") or payload[:2] == b"\xff\xfb":
            return ".mp3"
        if payload.startswith(b"OggS"):
            return ".ogg"
        if payload.startswith(SILK_MAGIC):
            return ".silk"
        suffix = Path(attachment.file_name).suffix.strip()
        if suffix:
            return suffix
        if attachment.mime_type.lower() == "audio/mpeg":
            return ".mp3"
        if attachment.mime_type.lower() == "audio/ogg":
            return ".ogg"
        return DEFAULT_AUDIO_EXT

    def _normalize_audio(self, source_path: Path, target_path: Path) -> None:
        ffmpeg_bin = resolve_ffmpeg_bin()
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
