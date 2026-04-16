from __future__ import annotations

import plistlib
import threading
import time
from pathlib import Path

from intel_center.audio_transcription import AudioAttachment
from intel_center.weixin_bridge import (
    CodexReply,
    CodexRunner,
    HealthCheckResult,
    WeixinBridgeState,
    WeixinCodexBridge,
    WeixinBridgeConfig,
    WeixinApiClient,
    WeixinPeerSession,
    assess_codex_ping_result,
    build_bridge_config,
    build_launch_agent_plist,
    build_client_version,
    bridge_service_label,
    default_service_log_paths,
    load_bridge_state,
    normalize_inbound_messages,
    overall_health_status,
    resolve_codex_bin,
    save_bridge_state,
    send_push_text,
    sync_workspace_mirror,
    summarize_bridge_log_health,
)


class FakeWeixinClient:
    def __init__(self, updates: dict):
        self.updates = updates
        self.sent_messages: list[dict] = []

    def get_updates(self, state):
        return self.updates

    def send_text(self, state, *, to_user_id: str, context_token: str, text: str):
        self.sent_messages.append(
            {
                "to_user_id": to_user_id,
                "context_token": context_token,
                "text": text,
                "token": state.token,
            }
        )
        return "client-1"

    def download_attachment(self, state, *, url: str, timeout_ms: int = 30_000):
        del state, timeout_ms
        return f"audio:{url}".encode("utf-8")


class FakeCodexRunner:
    def __init__(self):
        self.calls: list[dict] = []

    def ask(self, user_prompt: str, *, thread_id: str = "") -> CodexReply:
        self.calls.append({"user_prompt": user_prompt, "thread_id": thread_id})
        return CodexReply(thread_id=thread_id or "thread-123", text=f"echo:{user_prompt}")


class FakeAudioTranscriber:
    def __init__(self, transcripts: list[str] | None = None):
        self.calls: list[dict] = []
        self.transcripts = transcripts or ["语音转写内容"]

    def transcribe_attachments(self, attachments, *, message_id: int | None, downloader):
        self.calls.append({"attachments": attachments, "message_id": message_id})
        return list(self.transcripts)


def test_build_client_version_matches_openclaw_encoding() -> None:
    assert build_client_version("2.1.8") == (2 << 16) | (1 << 8) | 8


def test_state_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = WeixinBridgeState(
        token="token-1",
        bot_account_id="bot-1",
        user_id="user-1",
        base_url="https://example.com",
        get_updates_buf="cursor-1",
        peers={"alice@im.wechat": WeixinPeerSession(user_id="alice@im.wechat", codex_thread_id="thread-1")},
    )
    save_bridge_state(path, state)
    loaded = load_bridge_state(path)
    assert loaded.token == "token-1"
    assert loaded.get_updates_buf == "cursor-1"
    assert loaded.peers["alice@im.wechat"].codex_thread_id == "thread-1"


def test_normalize_inbound_messages_extracts_text() -> None:
    payload = {
        "msgs": [
            {
                "from_user_id": "alice@im.wechat",
                "to_user_id": "bot@im.bot",
                "context_token": "ctx-1",
                "message_id": 123,
                "create_time_ms": 456,
                "item_list": [
                    {"type": 1, "text_item": {"text": "hello"}},
                    {"type": 1, "text_item": {"text": "world"}},
                ],
            }
        ]
    }
    messages = normalize_inbound_messages(payload)
    assert len(messages) == 1
    assert messages[0].text == "hello\nworld"
    assert messages[0].context_token == "ctx-1"


def test_normalize_inbound_messages_extracts_audio_attachments() -> None:
    payload = {
        "msgs": [
            {
                "from_user_id": "alice@im.wechat",
                "to_user_id": "bot@im.bot",
                "context_token": "ctx-voice",
                "message_id": 124,
                "create_time_ms": 457,
                "item_list": [
                    {
                        "type": 34,
                        "voice_item": {
                            "download_url": "https://example.org/voice.amr",
                            "file_name": "voice.amr",
                            "duration_ms": 2100,
                        },
                    }
                ],
            }
        ]
    }
    messages = normalize_inbound_messages(payload)
    assert len(messages) == 1
    assert messages[0].audio_attachments
    assert messages[0].audio_attachments[0].download_url == "https://example.org/voice.amr"
    assert messages[0].audio_attachments[0].file_name == "voice.amr"


def test_bridge_handle_once_binds_peer_to_codex_thread(tmp_path: Path) -> None:
    state_path = tmp_path / "bridge-state.json"
    save_bridge_state(
        state_path,
        WeixinBridgeState(token="token-1", bot_account_id="bot-1", user_id="user-1", base_url="https://example.com"),
    )
    updates = {
        "ret": 0,
        "get_updates_buf": "cursor-2",
        "msgs": [
            {
                "from_user_id": "alice@im.wechat",
                "to_user_id": "bot@im.bot",
                "context_token": "ctx-2",
                "message_id": 88,
                "create_time_ms": 123456,
                "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
            }
        ],
    }
    client = FakeWeixinClient(updates)
    runner = FakeCodexRunner()
    config = build_bridge_config(workspace=tmp_path, state_path=state_path)
    bridge = WeixinCodexBridge(config, client=client, runner=runner)

    replies = bridge.handle_once()

    assert len(replies) == 1
    assert replies[0]["thread_id"] == "thread-123"
    assert runner.calls == [{"user_prompt": "你好", "thread_id": ""}]
    assert client.sent_messages[0]["to_user_id"] == "alice@im.wechat"
    assert client.sent_messages[0]["context_token"] == "ctx-2"
    assert client.sent_messages[0]["text"] == "echo:你好"

    loaded = load_bridge_state(state_path)
    assert loaded.get_updates_buf == "cursor-2"
    assert loaded.peers["alice@im.wechat"].codex_thread_id == "thread-123"
    assert loaded.peers["alice@im.wechat"].context_token == "ctx-2"


def test_bridge_reuses_existing_codex_thread(tmp_path: Path) -> None:
    state_path = tmp_path / "bridge-state.json"
    save_bridge_state(
        state_path,
        WeixinBridgeState(
            token="token-1",
            bot_account_id="bot-1",
            user_id="user-1",
            base_url="https://example.com",
            peers={"alice@im.wechat": WeixinPeerSession(user_id="alice@im.wechat", codex_thread_id="thread-1", context_token="ctx-old")},
        ),
    )
    updates = {
        "ret": 0,
        "get_updates_buf": "cursor-3",
        "msgs": [
            {
                "from_user_id": "alice@im.wechat",
                "to_user_id": "bot@im.bot",
                "context_token": "ctx-new",
                "message_id": 89,
                "create_time_ms": 123457,
                "item_list": [{"type": 1, "text_item": {"text": "继续"}}],
            }
        ],
    }
    client = FakeWeixinClient(updates)
    runner = FakeCodexRunner()
    config = build_bridge_config(workspace=tmp_path, state_path=state_path)
    bridge = WeixinCodexBridge(config, client=client, runner=runner)

    bridge.handle_once()

    assert runner.calls == [{"user_prompt": "继续", "thread_id": "thread-1"}]
    loaded = load_bridge_state(state_path)
    assert loaded.peers["alice@im.wechat"].context_token == "ctx-new"


def test_bridge_transcribes_audio_before_forwarding_to_codex(tmp_path: Path) -> None:
    state_path = tmp_path / "bridge-state.json"
    save_bridge_state(
        state_path,
        WeixinBridgeState(token="token-1", bot_account_id="bot-1", user_id="user-1", base_url="https://example.com"),
    )
    updates = {
        "ret": 0,
        "get_updates_buf": "cursor-audio",
        "msgs": [
            {
                "from_user_id": "alice@im.wechat",
                "to_user_id": "bot@im.bot",
                "context_token": "ctx-audio",
                "message_id": 188,
                "create_time_ms": 223344,
                "item_list": [
                    {
                        "type": 34,
                        "voice_item": {
                            "download_url": "https://example.org/voice.amr",
                            "file_name": "voice.amr",
                        },
                    }
                ],
            }
        ],
    }
    client = FakeWeixinClient(updates)
    runner = FakeCodexRunner()
    transcriber = FakeAudioTranscriber(["这是微信语音的转写"])
    config = build_bridge_config(workspace=tmp_path, state_path=state_path)
    bridge = WeixinCodexBridge(config, client=client, runner=runner, audio_transcriber=transcriber)

    replies = bridge.handle_once()

    assert len(replies) == 1
    assert transcriber.calls[0]["message_id"] == 188
    assert runner.calls == [{"user_prompt": "[WeChat voice transcript 1]\n这是微信语音的转写", "thread_id": ""}]
    assert client.sent_messages[0]["text"] == "echo:[WeChat voice transcript 1]\n这是微信语音的转写"


def test_build_bridge_config_uses_fast_codex_defaults(tmp_path: Path) -> None:
    config = build_bridge_config(workspace=tmp_path, state_path=tmp_path / "state.json")
    assert config.codex_model == "gpt-5.4-mini"
    assert config.codex_reasoning_effort == "medium"
    assert config.codex_workspace == tmp_path.resolve()
    assert config.transcription_backend == "local"
    assert config.transcribe_model == "small"


def test_build_bridge_config_creates_ascii_mirror_for_non_ascii_workspace(tmp_path: Path) -> None:
    source = tmp_path / "情报中心"
    source.mkdir()
    (source / "README.md").write_text("mirror me", encoding="utf-8")
    mirror_root = tmp_path / "mirrors"

    config = build_bridge_config(
        workspace=source,
        state_path=tmp_path / "state.json",
        mirror_root=mirror_root,
    )

    assert config.workspace == source.resolve()
    assert config.codex_workspace != source.resolve()
    assert str(config.codex_workspace).isascii()
    assert (config.codex_workspace / "README.md").read_text(encoding="utf-8") == "mirror me"


def test_codex_runner_builds_fast_command(tmp_path: Path) -> None:
    config = WeixinBridgeConfig(
        state_path=tmp_path / "state.json",
        workspace=tmp_path,
        codex_workspace=tmp_path,
        codex_bin="codex",
        codex_model="gpt-5.4-mini",
        codex_reasoning_effort="medium",
        codex_sandbox="read-only",
    )
    runner = CodexRunner(config)

    command = runner._build_command("hello", thread_id="")

    assert command[:5] == ["codex", "--disable", "plugins", "--disable", "shell_snapshot"]
    assert command[5] == "exec"
    assert "gpt-5.4-mini" in command
    assert "-c" in command
    assert 'model_reasoning_effort="medium"' in command
    assert "--sandbox" in command
    assert "hello" in command[-1]


def test_build_launch_agent_plist_contains_service_metadata(tmp_path: Path) -> None:
    label = bridge_service_label(tmp_path / "情报中心")
    stdout_path, stderr_path = default_service_log_paths(label, root=tmp_path / "runtime")
    plist_bytes = build_launch_agent_plist(
        label=label,
        program_arguments=[
            "/usr/bin/python3",
            "/repo/scripts/run_weixin_bridge.py",
            "serve",
            "--workspace",
            "/repo",
        ],
        working_directory=tmp_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    payload = plistlib.loads(plist_bytes)

    assert payload["Label"] == label
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is True
    assert payload["ProgramArguments"][2] == "serve"
    assert payload["StandardOutPath"] == str(stdout_path)
    assert payload["StandardErrorPath"] == str(stderr_path)
    assert payload["WorkingDirectory"] == str(tmp_path)


def test_summarize_bridge_log_health_ignores_plugin_noise() -> None:
    result = summarize_bridge_log_health(
        "\n".join(
            [
                "WARN codex_core::plugins::manifest: ignoring interface.defaultPrompt",
                "ERROR rmcp::transport::worker: worker quit with fatal: Transport channel closed",
            ]
        )
    )

    assert result.status == "pass"


def test_assess_codex_ping_result_marks_slow_roundtrip_as_warn() -> None:
    result = assess_codex_ping_result("OK", elapsed_seconds=9.2)
    assert result.status == "warn"
    assert "9.2s" in result.detail


def test_overall_health_status_prefers_fail_over_warn() -> None:
    status = overall_health_status(
        [
            HealthCheckResult(name="service", status="warn", detail="slow"),
            HealthCheckResult(name="codex", status="fail", detail="timeout"),
        ]
    )
    assert status == "fail"


def test_resolve_codex_bin_returns_absolute_path_for_existing_binary() -> None:
    resolved = resolve_codex_bin("python3")
    assert Path(resolved).is_absolute()


def test_send_push_text_targets_known_peers(tmp_path: Path) -> None:
    state_path = tmp_path / "bridge-state.json"
    save_bridge_state(
        state_path,
        WeixinBridgeState(
            token="token-1",
            bot_account_id="bot-1",
            user_id="user-1",
            base_url="https://example.com",
            peers={
                "alice@im.wechat": WeixinPeerSession(user_id="alice@im.wechat", context_token="ctx-a"),
                "bob@im.wechat": WeixinPeerSession(user_id="bob@im.wechat", context_token="ctx-b"),
            },
        ),
    )
    client = FakeWeixinClient({"ret": 0})
    config = build_bridge_config(workspace=tmp_path, state_path=state_path)

    delivered = send_push_text(config, "hello peers", peer_user_ids=["bob@im.wechat"], client=client)

    assert delivered == ["bob@im.wechat"]
    assert client.sent_messages == [
        {
            "to_user_id": "bob@im.wechat",
            "context_token": "ctx-b",
            "text": "hello peers",
            "token": "token-1",
        }
    ]


def test_sync_workspace_mirror_serializes_concurrent_calls(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("mirror me", encoding="utf-8")
    mirror = tmp_path / "mirror"
    active_calls = 0
    max_active_calls = 0
    call_guard = threading.Lock()

    def fake_run(command, *, text, capture_output, check):
        nonlocal active_calls, max_active_calls
        del command, text, capture_output, check
        with call_guard:
            active_calls += 1
            max_active_calls = max(max_active_calls, active_calls)
        time.sleep(0.05)
        mirror.mkdir(parents=True, exist_ok=True)
        (mirror / "README.md").write_text("mirror me", encoding="utf-8")
        with call_guard:
            active_calls -= 1

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr("intel_center.weixin_bridge.shutil.which", lambda value: "/usr/bin/rsync")
    monkeypatch.setattr("intel_center.weixin_bridge.subprocess.run", fake_run)

    thread_one = threading.Thread(target=sync_workspace_mirror, args=(source, mirror))
    thread_two = threading.Thread(target=sync_workspace_mirror, args=(source, mirror))
    thread_one.start()
    thread_two.start()
    thread_one.join()
    thread_two.join()

    assert max_active_calls == 1
    assert (mirror / "README.md").read_text(encoding="utf-8") == "mirror me"


def test_weixin_get_updates_treats_timeout_as_empty_poll(tmp_path: Path, monkeypatch) -> None:
    config = build_bridge_config(workspace=tmp_path, state_path=tmp_path / "state.json")
    client = WeixinApiClient(config)
    state = WeixinBridgeState(token="token-1", base_url="https://example.com", get_updates_buf="cursor-1")

    def raise_timeout(**kwargs):
        del kwargs
        raise TimeoutError("timed out")

    monkeypatch.setattr(client, "_post_json", raise_timeout)

    updates = client.get_updates(state)

    assert updates == {"ret": 0, "get_updates_buf": "cursor-1", "msgs": []}


def test_weixin_send_text_raises_when_api_rejects_message(tmp_path: Path, monkeypatch) -> None:
    config = build_bridge_config(workspace=tmp_path, state_path=tmp_path / "state.json")
    client = WeixinApiClient(config)
    state = WeixinBridgeState(token="token-1", base_url="https://example.com")

    def reject_message(**kwargs):
        del kwargs
        return {"ret": -2}

    monkeypatch.setattr(client, "_post_json", reject_message)

    try:
        client.send_text(state, to_user_id="alice@im.wechat", context_token="ctx-1", text="hello")
    except Exception as exc:
        assert "ret=-2" in str(exc)
        assert "message the bot again" in str(exc)
    else:
        raise AssertionError("Expected send_text to raise when Weixin rejects outbound delivery.")
