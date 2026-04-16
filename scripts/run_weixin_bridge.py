#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from intel_center.weixin_bridge import (
    CodexRunner,
    HealthCheckResult,
    WeixinApiClient,
    WeixinBridgeError,
    WeixinBridgeState,
    WeixinCodexBridge,
    assess_codex_ping_result,
    build_launch_agent_plist,
    build_bridge_config,
    bridge_service_label,
    default_launch_agent_path,
    default_service_log_paths,
    default_state_path,
    load_bridge_state,
    overall_health_status,
    resolve_codex_bin,
    save_bridge_state,
    summarize_bridge_log_health,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bridge WeChat messages into Codex CLI sessions.")
    parser.add_argument(
        "command",
        choices=[
            "login",
            "once",
            "serve",
            "service-start",
            "service-stop",
            "service-restart",
            "service-status",
            "service-logs",
            "health-check",
        ],
        help="Action to run.",
    )
    parser.add_argument("--state-path", type=Path, default=default_state_path(), help="Path to bridge state JSON.")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="Workspace passed to Codex CLI.")
    parser.add_argument("--codex-bin", default="codex", help="Path to the codex executable.")
    parser.add_argument("--codex-model", help="Optional Codex model override.")
    parser.add_argument(
        "--codex-reasoning-effort",
        default="medium",
        help="Reasoning effort override for Codex CLI. Defaults to medium for faster chat replies.",
    )
    parser.add_argument(
        "--codex-sandbox",
        default="read-only",
        choices=["read-only", "workspace-write", "danger-full-access"],
        help="Sandbox mode for new Codex threads.",
    )
    parser.add_argument(
        "--mirror-root",
        type=Path,
        help="Optional root directory for the optimized ASCII Codex mirror workspace.",
    )
    parser.add_argument(
        "--audio-cache-root",
        type=Path,
        help="Optional directory for cached WeChat voice attachments and transcripts.",
    )
    parser.add_argument(
        "--no-optimize-latency",
        action="store_true",
        help="Disable the ASCII mirror optimization and run Codex directly in the source workspace.",
    )
    parser.add_argument(
        "--disable-audio-transcription",
        action="store_true",
        help="Disable automatic transcription for inbound WeChat voice messages.",
    )
    parser.add_argument(
        "--transcription-backend",
        choices=["local", "openai"],
        default="local",
        help="Voice transcription backend. Defaults to local offline faster-whisper.",
    )
    parser.add_argument(
        "--transcribe-cli",
        help="Path to the transcribe_diarize.py CLI used for voice transcription.",
    )
    parser.add_argument(
        "--transcribe-model",
        default="small",
        help="Transcription model. For local backend, use a faster-whisper model such as tiny/base/small.",
    )
    parser.add_argument(
        "--transcribe-language",
        default="zh",
        help="Language hint passed to the transcription CLI. Defaults to zh.",
    )
    parser.add_argument("--allow-from", action="append", default=[], help="Restrict bridge to specific WeChat user IDs.")
    parser.add_argument("--preamble", help="Extra prompt prefix injected before each WeChat message.")
    parser.add_argument("--timeout-seconds", type=int, default=480, help="QR login wait timeout.")
    parser.add_argument("--idle-sleep", type=float, default=0.1, help="Sleep between long-poll cycles in serve mode.")
    parser.add_argument("--log-lines", type=int, default=60, help="Number of log lines to show for service-logs.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = build_bridge_config(
        workspace=args.workspace,
        state_path=args.state_path,
        codex_bin=args.codex_bin,
        codex_model=args.codex_model,
        codex_reasoning_effort=args.codex_reasoning_effort,
        codex_sandbox=args.codex_sandbox,
        optimize_latency=not args.no_optimize_latency,
        mirror_root=args.mirror_root,
        audio_cache_root=args.audio_cache_root,
        preamble=args.preamble,
        allow_from=args.allow_from,
        enable_audio_transcription=not args.disable_audio_transcription,
        transcription_backend=args.transcription_backend,
        transcribe_cli=args.transcribe_cli,
        transcribe_model=args.transcribe_model,
        transcribe_language=args.transcribe_language,
    )
    bridge = WeixinCodexBridge(config)

    try:
        if args.command == "login":
            return run_login(config, timeout_seconds=args.timeout_seconds)
        if args.command == "once":
            replies = bridge.handle_once()
            print(f"Handled {len(replies)} reply/replies.")
            for reply in replies:
                print(f"- {reply['from_user_id']} -> {reply['thread_id']}")
            return 0
        if args.command == "serve":
            while True:
                replies = bridge.handle_once()
                if replies:
                    print(f"[{time.strftime('%H:%M:%S')}] delivered {len(replies)} reply/replies.")
                time.sleep(args.idle_sleep)
        if args.command == "service-start":
            return run_service_start(args, config)
        if args.command == "service-stop":
            return run_service_stop(config)
        if args.command == "service-restart":
            run_service_stop(config, quiet=True)
            return run_service_start(args, config)
        if args.command == "service-status":
            return run_service_status(config)
        if args.command == "service-logs":
            return run_service_logs(config, line_count=args.log_lines)
        if args.command == "health-check":
            return run_health_check(config, line_count=args.log_lines)
        return 0
    except KeyboardInterrupt:
        return 130
    except WeixinBridgeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def run_login(config, *, timeout_seconds: int) -> int:
    client = WeixinApiClient(config)
    qr = client.start_login()
    print("Scan this URL in WeChat to authorize the bridge:")
    print(qr["qrcode_url"])
    result = client.wait_for_login(qr["qrcode"], timeout_seconds=timeout_seconds)
    state = WeixinBridgeState(
        token=result["token"],
        bot_account_id=result["bot_account_id"],
        user_id=result["user_id"],
        base_url=result["base_url"],
    )
    save_bridge_state(config.state_path, state)
    print(f"Connected bot account: {state.bot_account_id}")
    print(f"WeChat user: {state.user_id}")
    print(f"State saved to: {config.state_path}")
    return 0


def run_service_start(args: argparse.Namespace, config) -> int:
    state = load_bridge_state(config.state_path)
    if not state.token:
        raise WeixinBridgeError("Bridge is not logged in. Run the login command first.")

    label = bridge_service_label(config.workspace)
    plist_path = default_launch_agent_path(label)
    stdout_path, stderr_path = default_service_log_paths(label)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text("", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")

    plist_bytes = build_launch_agent_plist(
        label=label,
        program_arguments=build_service_program_arguments(args),
        working_directory=config.codex_workspace,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    plist_path.write_bytes(plist_bytes)

    domain = launchctl_domain_target(label)
    run_launchctl(["bootout", domain], check=False)
    run_launchctl(["bootstrap", f"gui/{os.getuid()}", str(plist_path)])
    run_launchctl(["kickstart", "-k", domain])

    print(f"Started service: {label}")
    print(f"Project workspace: {config.workspace}")
    print(f"Codex workspace: {config.codex_workspace}")
    print(f"LaunchAgent: {plist_path}")
    print(f"Logs: {stdout_path} | {stderr_path}")
    return 0


def run_service_stop(config, *, quiet: bool = False) -> int:
    label = bridge_service_label(config.workspace)
    domain = launchctl_domain_target(label)
    process = run_launchctl(["bootout", domain], check=False)
    if not quiet:
        if process.returncode == 0:
            print(f"Stopped service: {label}")
        else:
            print(f"Service not running: {label}")
    return 0


def run_service_status(config) -> int:
    label = bridge_service_label(config.workspace)
    domain = launchctl_domain_target(label)
    plist_path = default_launch_agent_path(label)
    stdout_path, stderr_path = default_service_log_paths(label)
    process = run_launchctl(["print", domain], check=False)
    status = "running" if process.returncode == 0 else ("installed" if plist_path.exists() else "not-installed")

    print(f"Service: {label}")
    print(f"Status: {status}")
    print(f"Project workspace: {config.workspace}")
    print(f"Codex workspace: {config.codex_workspace}")
    print(f"State file: {config.state_path}")
    print(f"LaunchAgent: {plist_path}")
    print(f"Logs: {stdout_path} | {stderr_path}")
    if process.returncode == 0:
        pid_match = re.search(r"\\bpid = (\\d+)", process.stdout)
        if pid_match:
            print(f"PID: {pid_match.group(1)}")
    return 0


def run_service_logs(config, *, line_count: int) -> int:
    label = bridge_service_label(config.workspace)
    stdout_path, stderr_path = default_service_log_paths(label)
    print(f"Service: {label}")
    print(f"stdout -> {stdout_path}")
    print(tail_file(stdout_path, line_count))
    print(f"stderr -> {stderr_path}")
    print(tail_file(stderr_path, line_count))
    return 0


def run_health_check(config, *, line_count: int) -> int:
    label = bridge_service_label(config.workspace)
    domain = launchctl_domain_target(label)
    stdout_path, stderr_path = default_service_log_paths(label)
    status_process = run_launchctl(["print", domain], check=False)
    state = load_bridge_state(config.state_path)
    results = []

    if status_process.returncode == 0:
        results.append(HealthCheckResult(name="service", status="pass", detail="launchd service is running."))
    else:
        results.append(HealthCheckResult(name="service", status="fail", detail="launchd service is not running."))

    if state.token and state.bot_account_id:
        results.append(HealthCheckResult(name="login", status="pass", detail=f"Logged in as {state.bot_account_id}."))
    else:
        results.append(
            HealthCheckResult(name="login", status="fail", detail="Missing WeChat login token. Run the login command again.")
        )

    mirror_detail = f"Codex workspace: {config.codex_workspace}"
    if config.optimize_latency and config.workspace != config.codex_workspace:
        if config.codex_workspace.exists() and str(config.codex_workspace).isascii():
            results.append(HealthCheckResult(name="workspace", status="pass", detail=mirror_detail))
        else:
            results.append(
                HealthCheckResult(name="workspace", status="fail", detail=f"ASCII mirror unavailable. Expected {config.codex_workspace}")
            )
    else:
        results.append(HealthCheckResult(name="workspace", status="warn", detail=f"Latency optimization is disabled. {mirror_detail}"))

    runner = CodexRunner(config)
    start = time.perf_counter()
    try:
        codex_reply = runner.ask("Reply with exactly: OK")
    except WeixinBridgeError as exc:
        results.append(HealthCheckResult(name="codex", status="fail", detail=str(exc)))
    else:
        elapsed_seconds = time.perf_counter() - start
        results.append(assess_codex_ping_result(codex_reply.text, elapsed_seconds=elapsed_seconds))

    stderr_health = summarize_bridge_log_health(tail_file(stderr_path, line_count))
    results.append(stderr_health)

    overall = overall_health_status(results)
    print(f"Health Check: {overall.upper()}")
    for result in results:
        print(f"- {result.name}: {result.status.upper()} - {result.detail}")
    print(f"- logs_path: {stdout_path}")
    print(f"- errors_path: {stderr_path}")
    return 1 if overall == "fail" else 0


def build_service_program_arguments(args: argparse.Namespace) -> list[str]:
    program_arguments = [
        sys.executable,
        str(Path(__file__).resolve()),
        "serve",
        "--workspace",
        str(args.workspace.expanduser().resolve()),
        "--state-path",
        str(args.state_path.expanduser().resolve()),
        "--codex-bin",
        resolve_codex_bin(args.codex_bin),
        "--codex-sandbox",
        args.codex_sandbox,
        "--codex-reasoning-effort",
        args.codex_reasoning_effort,
        "--idle-sleep",
        str(args.idle_sleep),
    ]
    if args.codex_model:
        program_arguments.extend(["--codex-model", args.codex_model])
    if args.mirror_root:
        program_arguments.extend(["--mirror-root", str(args.mirror_root.expanduser().resolve())])
    if args.no_optimize_latency:
        program_arguments.append("--no-optimize-latency")
    for allowed in args.allow_from:
        program_arguments.extend(["--allow-from", allowed])
    if args.preamble:
        program_arguments.extend(["--preamble", args.preamble])
    return program_arguments


def launchctl_domain_target(label: str) -> str:
    return f"gui/{os.getuid()}/{label}"


def run_launchctl(arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        ["launchctl", *arguments],
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if check and process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "unknown launchctl error"
        raise WeixinBridgeError(f"launchctl {' '.join(arguments)} failed: {detail}")
    return process


def tail_file(path: Path, line_count: int) -> str:
    if not path.exists():
        return "(log file not created yet)"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return "(empty)"
    return "\n".join(lines[-line_count:])


if __name__ == "__main__":
    raise SystemExit(main())
