from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import json
import os
import plistlib
import random
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from .audio_transcription import (
    DEFAULT_TRANSCRIBE_LANGUAGE,
    DEFAULT_TRANSCRIBE_MODEL,
    AudioAttachment,
    AudioTranscriptionError,
    WeixinAudioTranscriber,
    default_transcribe_cli_path,
)

BRIDGE_VERSION = "0.1.0"
DEFAULT_WEIXIN_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_BOT_TYPE = "3"
DEFAULT_ILINK_APP_ID = "bot"
DEFAULT_ILINK_APP_VERSION = "2.1.8"
DEFAULT_LONG_POLL_TIMEOUT_MS = 35_000
DEFAULT_CODEX_MODEL = "gpt-5.4-mini"
DEFAULT_CODEX_REASONING_EFFORT = "medium"

_MIRROR_LOCK_REGISTRY: dict[str, threading.Lock] = {}
_MIRROR_LOCK_REGISTRY_GUARD = threading.Lock()


class WeixinBridgeError(RuntimeError):
    """Raised when the bridge encounters a protocol or backend failure."""


@dataclass(slots=True)
class WeixinPeerSession:
    user_id: str
    context_token: str = ""
    codex_thread_id: str = ""
    last_message_id: int | None = None
    last_seen_at: str = ""


@dataclass(slots=True)
class WeixinBridgeState:
    token: str = ""
    bot_account_id: str = ""
    user_id: str = ""
    base_url: str = DEFAULT_WEIXIN_BASE_URL
    get_updates_buf: str = ""
    peers: dict[str, WeixinPeerSession] = field(default_factory=dict)


@dataclass(slots=True)
class WeixinBridgeConfig:
    state_path: Path
    workspace: Path
    codex_workspace: Path
    codex_bin: str = "codex"
    codex_model: str | None = DEFAULT_CODEX_MODEL
    codex_reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT
    codex_disabled_features: tuple[str, ...] = ("plugins", "shell_snapshot")
    codex_sandbox: str = "read-only"
    ilink_app_id: str = DEFAULT_ILINK_APP_ID
    ilink_app_version: str = DEFAULT_ILINK_APP_VERSION
    long_poll_timeout_ms: int = DEFAULT_LONG_POLL_TIMEOUT_MS
    optimize_latency: bool = True
    mirror_root: Path | None = None
    audio_cache_root: Path | None = None
    preamble: str = (
        "You are replying inside a WeChat direct-message conversation. "
        "Keep responses concise, plain-text friendly, and avoid markdown tables unless requested."
    )
    allow_from: set[str] = field(default_factory=set)
    enable_audio_transcription: bool = True
    transcribe_cli: str = ""
    transcribe_model: str = DEFAULT_TRANSCRIBE_MODEL
    transcribe_language: str = DEFAULT_TRANSCRIBE_LANGUAGE


@dataclass(slots=True)
class WeixinInboundMessage:
    from_user_id: str
    to_user_id: str
    context_token: str
    text: str
    message_id: int | None
    create_time_ms: int | None
    audio_attachments: list[AudioAttachment] = field(default_factory=list)


@dataclass(slots=True)
class CodexReply:
    thread_id: str
    text: str


@dataclass(slots=True)
class HealthCheckResult:
    name: str
    status: str
    detail: str


def default_state_path() -> Path:
    return Path.home() / ".codex" / "weixin-bridge" / "state.json"


def default_bridge_root() -> Path:
    return Path.home() / ".codex" / "weixin-bridge"


def default_mirror_root() -> Path:
    return default_bridge_root() / "mirrors"


def default_launch_agent_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def default_audio_cache_root() -> Path:
    return default_bridge_root() / "audio"


def load_bridge_state(path: Path) -> WeixinBridgeState:
    if not path.exists():
        return WeixinBridgeState()
    blob = json.loads(path.read_text(encoding="utf-8"))
    peers = {
        user_id: WeixinPeerSession(**peer_blob)
        for user_id, peer_blob in blob.get("peers", {}).items()
    }
    return WeixinBridgeState(
        token=blob.get("token", ""),
        bot_account_id=blob.get("bot_account_id", ""),
        user_id=blob.get("user_id", ""),
        base_url=blob.get("base_url", DEFAULT_WEIXIN_BASE_URL),
        get_updates_buf=blob.get("get_updates_buf", ""),
        peers=peers,
    )


def save_bridge_state(path: Path, state: WeixinBridgeState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "token": state.token,
        "bot_account_id": state.bot_account_id,
        "user_id": state.user_id,
        "base_url": state.base_url,
        "get_updates_buf": state.get_updates_buf,
        "peers": {user_id: asdict(peer) for user_id, peer in state.peers.items()},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def bridge_service_label(workspace: Path) -> str:
    source_workspace = workspace.expanduser().resolve()
    slug = _ascii_slug(source_workspace.name)
    digest = hashlib.sha1(str(source_workspace).encode("utf-8")).hexdigest()[:10]
    return f"dev.codex.weixin-bridge.{slug}-{digest}"


def default_service_log_paths(label: str, *, root: Path | None = None) -> tuple[Path, Path]:
    base_root = (root or default_bridge_root()).expanduser()
    log_root = base_root / "logs"
    return log_root / f"{label}.out.log", log_root / f"{label}.err.log"


def default_launch_agent_path(label: str, *, root: Path | None = None) -> Path:
    launch_agent_root = (root or default_launch_agent_dir()).expanduser()
    return launch_agent_root / f"{label}.plist"


def build_launch_agent_plist(
    *,
    label: str,
    program_arguments: list[str],
    working_directory: Path,
    stdout_path: Path,
    stderr_path: Path,
) -> bytes:
    payload = {
        "Label": label,
        "ProgramArguments": program_arguments,
        "WorkingDirectory": str(working_directory),
        "RunAtLoad": True,
        "KeepAlive": True,
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False)


def needs_ascii_mirror(workspace: Path) -> bool:
    return not str(workspace.expanduser().resolve()).isascii()


def mirror_path_for_workspace(workspace: Path, *, root: Path | None = None) -> Path:
    source_workspace = workspace.expanduser().resolve()
    slug = _ascii_slug(source_workspace.name)
    digest = hashlib.sha1(str(source_workspace).encode("utf-8")).hexdigest()[:10]
    mirror_root = (root or default_mirror_root()).expanduser()
    return mirror_root / f"{slug}-{digest}"


def ensure_codex_workspace(
    workspace: Path,
    *,
    optimize_latency: bool = True,
    mirror_root: Path | None = None,
) -> Path:
    source_workspace = workspace.expanduser().resolve()
    if not optimize_latency or not needs_ascii_mirror(source_workspace):
        return source_workspace

    mirror_workspace = mirror_path_for_workspace(source_workspace, root=mirror_root)
    sync_workspace_mirror(source_workspace, mirror_workspace)
    return mirror_workspace.resolve()


def sync_workspace_mirror(source_workspace: Path, mirror_workspace: Path) -> None:
    source_workspace = source_workspace.expanduser().resolve()
    mirror_workspace = mirror_workspace.expanduser()
    mirror_workspace.parent.mkdir(parents=True, exist_ok=True)
    rsync_bin = shutil.which("rsync")
    if not rsync_bin:
        raise WeixinBridgeError("rsync is required to maintain the optimized ASCII Codex mirror.")
    with _acquire_mirror_sync_lock(mirror_workspace):
        command = [
            rsync_bin,
            "-a",
            "--delete",
            "--exclude",
            ".git",
            "--exclude",
            ".DS_Store",
            f"{source_workspace}/",
            f"{mirror_workspace}/",
        ]
        process = subprocess.run(command, text=True, capture_output=True, check=False)
        if process.returncode != 0:
            detail = process.stderr.strip() or process.stdout.strip()
            raise WeixinBridgeError(f"Failed to sync ASCII Codex mirror: {detail}")


@contextlib.contextmanager
def _acquire_mirror_sync_lock(mirror_workspace: Path):
    lock_path = mirror_workspace.parent / f".{mirror_workspace.name}.sync.lock"
    with _MIRROR_LOCK_REGISTRY_GUARD:
        thread_lock = _MIRROR_LOCK_REGISTRY.setdefault(str(lock_path), threading.Lock())

    with thread_lock:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _ascii_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return slug or "workspace"


def resolve_codex_bin(codex_bin: str) -> str:
    if os.sep in codex_bin or codex_bin.startswith("."):
        candidate = Path(codex_bin).expanduser()
        if candidate.exists():
            return str(candidate.resolve())
    resolved = shutil.which(codex_bin)
    if resolved:
        return resolved
    raise WeixinBridgeError(f"Unable to find executable for codex_bin={codex_bin!r}.")


def summarize_bridge_log_health(log_text: str) -> HealthCheckResult:
    actionable_markers = (
        "WeixinBridgeError",
        "Codex CLI failed",
        "Failed to sync ASCII Codex mirror",
        "Traceback",
        "Weixin API ",
    )
    hits = [line.strip() for line in log_text.splitlines() if any(marker in line for marker in actionable_markers)]
    if not hits:
        return HealthCheckResult(name="logs", status="pass", detail="No bridge-specific errors in recent logs.")
    return HealthCheckResult(name="logs", status="warn", detail=f"Recent bridge errors: {hits[-1]}")


def assess_codex_ping_result(reply_text: str, *, elapsed_seconds: float) -> HealthCheckResult:
    rounded = f"{elapsed_seconds:.1f}s"
    cleaned = reply_text.strip()
    if not cleaned:
        return HealthCheckResult(name="codex", status="fail", detail=f"Codex returned an empty reply after {rounded}.")
    if elapsed_seconds > 15:
        return HealthCheckResult(name="codex", status="fail", detail=f"Codex ping succeeded but was too slow: {rounded}.")
    if elapsed_seconds > 8:
        return HealthCheckResult(name="codex", status="warn", detail=f"Codex ping succeeded but was slow: {rounded}.")
    if cleaned.upper() != "OK":
        return HealthCheckResult(name="codex", status="warn", detail=f"Codex replied in {rounded}, but returned {cleaned!r}.")
    return HealthCheckResult(name="codex", status="pass", detail=f"Codex ping returned OK in {rounded}.")


def overall_health_status(results: list[HealthCheckResult]) -> str:
    statuses = {result.status for result in results}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "pass"


def send_push_text(
    config: WeixinBridgeConfig,
    text: str,
    *,
    peer_user_ids: list[str] | None = None,
    client: WeixinApiClient | None = None,
) -> list[str]:
    state = load_bridge_state(config.state_path)
    if not state.token:
        raise WeixinBridgeError("Bridge is not logged in. Run the login command first.")
    targets = _resolve_push_targets(state, peer_user_ids=peer_user_ids)
    sender = client or WeixinApiClient(config)
    delivered: list[str] = []
    payload = truncate_for_wechat(text)
    for peer in targets:
        sender.send_text(
            state,
            to_user_id=peer.user_id,
            context_token=peer.context_token,
            text=payload,
        )
        delivered.append(peer.user_id)
    return delivered


def resolve_push_user_ids(
    config: WeixinBridgeConfig,
    *,
    peer_user_ids: list[str] | None = None,
) -> list[str]:
    state = load_bridge_state(config.state_path)
    return [peer.user_id for peer in _resolve_push_targets(state, peer_user_ids=peer_user_ids)]


def _resolve_push_targets(state: WeixinBridgeState, peer_user_ids: list[str] | None = None) -> list[WeixinPeerSession]:
    if peer_user_ids:
        targets = []
        missing = []
        for user_id in peer_user_ids:
            peer = state.peers.get(user_id)
            if peer:
                targets.append(peer)
            else:
                missing.append(user_id)
        if missing:
            raise WeixinBridgeError(f"Unknown WeChat peer(s): {', '.join(missing)}")
        return targets
    if not state.peers:
        raise WeixinBridgeError("No known WeChat peers are available for proactive pushes yet.")
    return list(state.peers.values())


class WeixinApiClient:
    def __init__(self, config: WeixinBridgeConfig):
        self.config = config

    def start_login(self, *, force: bool = False) -> dict[str, str]:
        del force
        query = urllib.parse.urlencode({"bot_type": DEFAULT_BOT_TYPE})
        url = f"{DEFAULT_WEIXIN_BASE_URL.rstrip('/')}/ilink/bot/get_bot_qrcode?{query}"
        request = urllib.request.Request(url, headers=self._common_headers(), method="GET")
        payload = json.loads(self._read_response(request, timeout=15_000))
        return {
            "qrcode": str(payload["qrcode"]),
            "qrcode_url": str(payload["qrcode_img_content"]),
        }

    def wait_for_login(self, qrcode: str, *, timeout_seconds: int = 480) -> dict[str, str]:
        deadline = time.time() + timeout_seconds
        current_base_url = DEFAULT_WEIXIN_BASE_URL
        while time.time() < deadline:
            query = urllib.parse.urlencode({"qrcode": qrcode})
            url = f"{current_base_url.rstrip('/')}/ilink/bot/get_qrcode_status?{query}"
            request = urllib.request.Request(url, headers=self._common_headers(), method="GET")
            try:
                payload = json.loads(self._read_response(request, timeout=self.config.long_poll_timeout_ms))
            except TimeoutError:
                continue

            status = payload.get("status", "wait")
            if status == "scaned_but_redirect" and payload.get("redirect_host"):
                current_base_url = f"https://{payload['redirect_host']}"
                continue
            if status in {"wait", "scaned"}:
                continue
            if status == "expired":
                raise WeixinBridgeError("WeChat QR code expired before confirmation.")
            if status == "confirmed":
                return {
                    "token": str(payload.get("bot_token", "")),
                    "bot_account_id": str(payload.get("ilink_bot_id", "")),
                    "user_id": str(payload.get("ilink_user_id", "")),
                    "base_url": str(payload.get("baseurl") or current_base_url),
                }
            raise WeixinBridgeError(f"Unexpected WeChat login status: {status}")
        raise WeixinBridgeError("Timed out while waiting for WeChat QR confirmation.")

    def get_updates(self, state: WeixinBridgeState) -> dict[str, Any]:
        body = {
            "get_updates_buf": state.get_updates_buf or "",
            "base_info": self._base_info(),
        }
        try:
            payload = self._post_json(
                base_url=state.base_url,
                endpoint="ilink/bot/getupdates",
                body=body,
                token=state.token,
                timeout_ms=self.config.long_poll_timeout_ms,
            )
        except TimeoutError:
            return {"ret": 0, "get_updates_buf": state.get_updates_buf or "", "msgs": []}
        if payload.get("ret") not in {None, 0} or payload.get("errcode") not in {None, 0}:
            raise WeixinBridgeError(
                f"Weixin getupdates failed: ret={payload.get('ret')} errcode={payload.get('errcode')} errmsg={payload.get('errmsg')}"
            )
        return payload

    def send_text(self, state: WeixinBridgeState, *, to_user_id: str, context_token: str, text: str) -> str:
        client_id = f"codex-weixin-{uuid4()}"
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": 2,
                "message_state": 2,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
                "context_token": context_token or None,
            },
            "base_info": self._base_info(),
        }
        payload = self._post_json(
            base_url=state.base_url,
            endpoint="ilink/bot/sendmessage",
            body=body,
            token=state.token,
            timeout_ms=15_000,
        )
        ret = payload.get("ret")
        errcode = payload.get("errcode")
        if ret not in {None, 0} or errcode not in {None, 0}:
            detail = f"ret={ret}" if errcode in {None, 0} else f"ret={ret} errcode={errcode}"
            if ret == -2 or errcode == -2:
                raise WeixinBridgeError(
                    f"Weixin sendmessage rejected the outbound message ({detail}). "
                    "The recipient likely needs to message the bot again before proactive delivery is allowed."
                )
            raise WeixinBridgeError(f"Weixin sendmessage failed: {detail}")
        return client_id

    def download_attachment(self, state: WeixinBridgeState, *, url: str, timeout_ms: int = 30_000) -> bytes:
        resolved_url = urllib.parse.urljoin(state.base_url.rstrip("/") + "/", url)
        headers = {
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": random_wechat_uin(),
            **self._common_headers(),
        }
        if state.token.strip():
            headers["Authorization"] = f"Bearer {state.token.strip()}"
        request = urllib.request.Request(resolved_url, headers=headers, method="GET")
        timeout_seconds = max(timeout_ms / 1000, 1)
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise WeixinBridgeError(f"Weixin attachment download failed with {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise WeixinBridgeError(f"Weixin attachment download error: {exc.reason}") from exc

    def _post_json(
        self,
        *,
        base_url: str,
        endpoint: str,
        body: dict[str, Any],
        token: str,
        timeout_ms: int,
    ) -> dict[str, Any]:
        encoded = json.dumps(body).encode("utf-8")
        url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        headers = self._auth_headers(token=token, content_length=len(encoded))
        request = urllib.request.Request(url, data=encoded, headers=headers, method="POST")
        raw = self._read_response(request, timeout=timeout_ms)
        return json.loads(raw)

    def _read_response(self, request: urllib.request.Request, *, timeout: int) -> str:
        timeout_seconds = max(timeout / 1000, 1)
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise WeixinBridgeError(f"Weixin API {exc.code}: {body}") from exc
        except TimeoutError as exc:
            raise TimeoutError("Weixin API request timed out.") from exc
        except urllib.error.URLError as exc:
            if "timed out" in str(exc.reason).lower():
                raise TimeoutError("Weixin API request timed out.") from exc
            raise WeixinBridgeError(f"Weixin API connection error: {exc.reason}") from exc

    def _base_info(self) -> dict[str, str]:
        return {"channel_version": BRIDGE_VERSION}

    def _common_headers(self) -> dict[str, str]:
        return {
            "iLink-App-Id": self.config.ilink_app_id,
            "iLink-App-ClientVersion": str(build_client_version(self.config.ilink_app_version)),
        }

    def _auth_headers(self, *, token: str, content_length: int) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Content-Length": str(content_length),
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": random_wechat_uin(),
            **self._common_headers(),
        }
        if token.strip():
            headers["Authorization"] = f"Bearer {token.strip()}"
        return headers


class CodexRunner:
    def __init__(self, config: WeixinBridgeConfig):
        self.config = config

    def ask(self, user_prompt: str, *, thread_id: str = "") -> CodexReply:
        prompt = self._build_prompt(user_prompt)
        workspace = self._prepare_workspace()
        command = self._build_command(prompt, thread_id=thread_id)

        process = subprocess.run(
            command,
            cwd=str(workspace),
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        if process.returncode != 0:
            raise WeixinBridgeError(f"Codex CLI failed: {process.stderr.strip() or process.stdout.strip()}")

        discovered_thread_id = thread_id
        reply_text = ""
        for line in process.stdout.splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "thread.started":
                discovered_thread_id = str(event.get("thread_id") or discovered_thread_id)
            item = event.get("item") or {}
            if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                reply_text = str(item.get("text", "")).strip()

        if not discovered_thread_id:
            raise WeixinBridgeError("Codex CLI did not return a thread_id.")
        if not reply_text:
            raise WeixinBridgeError("Codex CLI returned no final message.")
        return CodexReply(thread_id=discovered_thread_id, text=reply_text)

    def _prepare_workspace(self) -> Path:
        workspace = ensure_codex_workspace(
            self.config.workspace,
            optimize_latency=self.config.optimize_latency,
            mirror_root=self.config.mirror_root,
        )
        self.config.codex_workspace = workspace
        return workspace

    def _build_command(self, prompt: str, *, thread_id: str = "") -> list[str]:
        command = [self.config.codex_bin]
        for feature in self.config.codex_disabled_features:
            command.extend(["--disable", feature])
        command.append("exec")
        if thread_id:
            command.extend(["resume", thread_id])
        if self.config.codex_model:
            command.extend(["--model", self.config.codex_model])
        if self.config.codex_reasoning_effort:
            command.extend(["-c", f'model_reasoning_effort="{self.config.codex_reasoning_effort}"'])
        command.extend(["--json", "--skip-git-repo-check"])
        if not thread_id:
            command.extend(["--sandbox", self.config.codex_sandbox])
        command.append(prompt)
        return command

    def _build_prompt(self, user_prompt: str) -> str:
        return f"{self.config.preamble}\n\nUser message from WeChat:\n{user_prompt.strip()}"


class WeixinCodexBridge:
    def __init__(
        self,
        config: WeixinBridgeConfig,
        *,
        client: WeixinApiClient | None = None,
        runner: CodexRunner | None = None,
        audio_transcriber: WeixinAudioTranscriber | None = None,
    ):
        self.config = config
        self.client = client or WeixinApiClient(config)
        self.runner = runner or CodexRunner(config)
        self.audio_transcriber = audio_transcriber or (
            WeixinAudioTranscriber(
                cache_root=config.audio_cache_root or default_audio_cache_root(),
                transcribe_cli=Path(config.transcribe_cli).expanduser() if config.transcribe_cli else default_transcribe_cli_path(),
                model=config.transcribe_model,
                language=config.transcribe_language,
            )
            if config.enable_audio_transcription
            else None
        )

    def login(self, *, timeout_seconds: int = 480) -> dict[str, str]:
        qr = self.client.start_login()
        result = self.client.wait_for_login(qr["qrcode"], timeout_seconds=timeout_seconds)
        state = load_bridge_state(self.config.state_path)
        state.token = result["token"]
        state.bot_account_id = result["bot_account_id"]
        state.user_id = result["user_id"]
        state.base_url = result["base_url"] or DEFAULT_WEIXIN_BASE_URL
        save_bridge_state(self.config.state_path, state)
        return {
            "qrcode_url": qr["qrcode_url"],
            "bot_account_id": state.bot_account_id,
            "user_id": state.user_id,
            "base_url": state.base_url,
        }

    def poll_once(self) -> list[WeixinInboundMessage]:
        state = load_bridge_state(self.config.state_path)
        if not state.token:
            raise WeixinBridgeError("Bridge is not logged in. Run the login command first.")

        updates = self.client.get_updates(state)
        if updates.get("get_updates_buf"):
            state.get_updates_buf = str(updates["get_updates_buf"])
            save_bridge_state(self.config.state_path, state)
        return normalize_inbound_messages(updates)

    def handle_once(self) -> list[dict[str, str]]:
        state = load_bridge_state(self.config.state_path)
        if not state.token:
            raise WeixinBridgeError("Bridge is not logged in. Run the login command first.")
        updates = self.client.get_updates(state)
        if updates.get("get_updates_buf"):
            state.get_updates_buf = str(updates["get_updates_buf"])
        messages = normalize_inbound_messages(updates)
        replies: list[dict[str, str]] = []
        for message in messages:
            if self.config.allow_from and message.from_user_id not in self.config.allow_from:
                continue

            peer = state.peers.get(message.from_user_id) or WeixinPeerSession(user_id=message.from_user_id)
            peer.context_token = message.context_token or peer.context_token
            peer.last_message_id = message.message_id
            peer.last_seen_at = iso_now()

            prompt_text = message.text.strip()
            if message.audio_attachments:
                prompt_text = self._merge_audio_transcripts(state, message, existing_text=prompt_text)
            if not prompt_text.strip():
                continue

            codex_reply = self.runner.ask(prompt_text, thread_id=peer.codex_thread_id)
            peer.codex_thread_id = codex_reply.thread_id
            state.peers[message.from_user_id] = peer
            self.client.send_text(
                state,
                to_user_id=message.from_user_id,
                context_token=peer.context_token,
                text=truncate_for_wechat(codex_reply.text),
            )
            replies.append(
                {
                    "from_user_id": message.from_user_id,
                    "thread_id": peer.codex_thread_id,
                    "reply": codex_reply.text,
                }
            )
        save_bridge_state(self.config.state_path, state)
        return replies

    def _merge_audio_transcripts(self, state: WeixinBridgeState, message: WeixinInboundMessage, *, existing_text: str) -> str:
        if not self.audio_transcriber:
            return existing_text
        try:
            transcripts = self.audio_transcriber.transcribe_attachments(
                message.audio_attachments,
                message_id=message.message_id,
                downloader=lambda url, path: path.write_bytes(
                    self.client.download_attachment(state, url=url)
                ),
            )
        except AudioTranscriptionError as exc:
            if not existing_text.strip():
                self.client.send_text(
                    state,
                    to_user_id=message.from_user_id,
                    context_token=message.context_token,
                    text=truncate_for_wechat(f"Voice message received, but transcription is unavailable: {exc}"),
                )
            return existing_text

        if not transcripts:
            return existing_text
        transcript_block = "\n\n".join(
            f"[WeChat voice transcript {index}]\n{text}" for index, text in enumerate(transcripts, start=1)
        )
        if existing_text.strip():
            return f"{existing_text.strip()}\n\n{transcript_block}"
        return transcript_block

    def serve_forever(self, *, idle_sleep_seconds: float = 1.0) -> None:
        while True:
            self.handle_once()
            time.sleep(idle_sleep_seconds)


def normalize_inbound_messages(payload: dict[str, Any]) -> list[WeixinInboundMessage]:
    messages: list[WeixinInboundMessage] = []
    for raw in payload.get("msgs", []) or []:
        if raw.get("message_type") == 2:
            continue
        from_user_id = str(raw.get("from_user_id") or "")
        to_user_id = str(raw.get("to_user_id") or "")
        if not from_user_id or not to_user_id:
            continue
        text_parts: list[str] = []
        audio_attachments = _extract_audio_attachments(raw)
        for item in raw.get("item_list", []) or []:
            if item.get("type") == 1 and item.get("text_item", {}).get("text"):
                text_parts.append(str(item["text_item"]["text"]))
        messages.append(
            WeixinInboundMessage(
                from_user_id=from_user_id,
                to_user_id=to_user_id,
                context_token=str(raw.get("context_token") or ""),
                text="\n".join(part.strip() for part in text_parts if part.strip()),
                message_id=raw.get("message_id"),
                create_time_ms=raw.get("create_time_ms"),
                audio_attachments=audio_attachments,
            )
        )
    return messages


def _extract_audio_attachments(raw_message: dict[str, Any]) -> list[AudioAttachment]:
    attachments: list[AudioAttachment] = []
    seen_urls: set[str] = set()
    for item in raw_message.get("item_list", []) or []:
        url = _find_audio_url(item)
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        attachments.append(
            AudioAttachment(
                download_url=url,
                file_name=_find_attachment_filename(item),
                mime_type=_find_mime_type(item),
                duration_ms=_find_duration_ms(item),
                item_type=str(item.get("type") or ""),
            )
        )
    return attachments


def _find_audio_url(node: Any, *, key_path: str = "") -> str:
    if isinstance(node, dict):
        for key, value in node.items():
            next_path = f"{key_path}.{key}" if key_path else str(key)
            lowered_key = str(key).lower()
            if isinstance(value, str) and value.strip():
                if _looks_like_audio_url(value, key_hint=next_path) or _looks_like_audio_url(value, key_hint=lowered_key):
                    return value.strip()
            found = _find_audio_url(value, key_path=next_path)
            if found:
                return found
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found = _find_audio_url(value, key_path=f"{key_path}[{index}]")
            if found:
                return found
    return ""


def _looks_like_audio_url(value: str, *, key_hint: str) -> bool:
    lowered_value = value.strip().lower()
    lowered_key = key_hint.lower()
    if not (lowered_value.startswith("http://") or lowered_value.startswith("https://") or lowered_value.startswith("/")):
        return False
    if any(token in lowered_key for token in ("voice", "audio", "media", "play", "download")):
        return True
    return lowered_value.endswith((".amr", ".mp3", ".m4a", ".wav", ".aac", ".ogg", ".opus", ".silk"))


def _find_attachment_filename(node: Any) -> str:
    for key in ("file_name", "filename", "name", "title"):
        value = _find_first_string(node, key)
        if value:
            return value
    return ""


def _find_mime_type(node: Any) -> str:
    for key in ("mime_type", "content_type"):
        value = _find_first_string(node, key)
        if value:
            return value
    return ""


def _find_duration_ms(node: Any) -> int | None:
    for key in ("duration_ms", "duration", "voice_duration", "audio_duration"):
        value = _find_first_scalar(node, key)
        if value in {None, ""}:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _find_first_string(node: Any, target_key: str) -> str:
    value = _find_first_scalar(node, target_key)
    return str(value).strip() if isinstance(value, str) and str(value).strip() else ""


def _find_first_scalar(node: Any, target_key: str) -> Any:
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() == target_key.lower() and not isinstance(value, (dict, list)):
                return value
            nested = _find_first_scalar(value, target_key)
            if nested is not None:
                return nested
    elif isinstance(node, list):
        for value in node:
            nested = _find_first_scalar(value, target_key)
            if nested is not None:
                return nested
    return None


def build_client_version(version: str) -> int:
    parts = version.split(".")
    major = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
    minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    patch = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    return ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)


def random_wechat_uin() -> str:
    return base64.b64encode(str(random.randint(0, 2**32 - 1)).encode("utf-8")).decode("utf-8")


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def truncate_for_wechat(text: str, *, limit: int = 3800) -> str:
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: limit - 1].rstrip()}…"


def build_bridge_config(
    *,
    workspace: Path,
    state_path: Path | None = None,
    codex_bin: str = "codex",
    codex_model: str | None = DEFAULT_CODEX_MODEL,
    codex_reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT,
    codex_sandbox: str = "read-only",
    optimize_latency: bool = True,
    mirror_root: Path | None = None,
    audio_cache_root: Path | None = None,
    preamble: str | None = None,
    allow_from: list[str] | None = None,
    enable_audio_transcription: bool = True,
    transcribe_cli: str | None = None,
    transcribe_model: str = DEFAULT_TRANSCRIBE_MODEL,
    transcribe_language: str = DEFAULT_TRANSCRIBE_LANGUAGE,
) -> WeixinBridgeConfig:
    source_workspace = workspace.expanduser().resolve()
    resolved_state_path = (state_path or default_state_path()).expanduser().resolve()
    resolved_mirror_root = (mirror_root or default_mirror_root()).expanduser()
    resolved_audio_cache_root = (audio_cache_root or default_audio_cache_root()).expanduser()
    resolved_codex_model = codex_model or DEFAULT_CODEX_MODEL
    resolved_reasoning_effort = codex_reasoning_effort or DEFAULT_CODEX_REASONING_EFFORT
    codex_workspace = ensure_codex_workspace(
        source_workspace,
        optimize_latency=optimize_latency,
        mirror_root=resolved_mirror_root,
    )
    return WeixinBridgeConfig(
        state_path=resolved_state_path,
        workspace=source_workspace,
        codex_workspace=codex_workspace,
        codex_bin=resolve_codex_bin(codex_bin),
        codex_model=resolved_codex_model,
        codex_reasoning_effort=resolved_reasoning_effort,
        codex_sandbox=codex_sandbox,
        optimize_latency=optimize_latency,
        mirror_root=resolved_mirror_root,
        audio_cache_root=resolved_audio_cache_root,
        preamble=preamble or WeixinBridgeConfig(state_path=Path("."), workspace=source_workspace, codex_workspace=codex_workspace).preamble,
        allow_from=set(allow_from or []),
        enable_audio_transcription=enable_audio_transcription,
        transcribe_cli=str((Path(transcribe_cli).expanduser() if transcribe_cli else default_transcribe_cli_path()).resolve()),
        transcribe_model=transcribe_model or DEFAULT_TRANSCRIBE_MODEL,
        transcribe_language=transcribe_language or DEFAULT_TRANSCRIBE_LANGUAGE,
    )
