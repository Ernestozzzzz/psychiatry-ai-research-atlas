from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha1
from pathlib import Path

from .models import RunResult


def build_weixin_brief_message(
    result: RunResult,
    *,
    max_curated_items: int = 5,
    max_raw_items: int = 3,
) -> str:
    lines: list[str] = []
    run_date = result.generated_at.date().isoformat()
    lines.append(f"AI + Mental Health Daily Brief | {run_date}")
    lines.append("")

    for bullet in result.executive_summary_bullets[:3]:
        lines.append(f"- {bullet}")

    curated_items = result.curated_items[:max_curated_items]
    if curated_items:
        lines.append("")
        lines.append("Curated")
        for index, item in enumerate(curated_items, start=1):
            venue = item.journal_name or item.source
            published = item.published_at.date().isoformat()
            lines.append(f"{index}. {item.title_en} | {venue} | {published}")

    raw_items = result.raw_intake_items[:max_raw_items]
    if raw_items:
        lines.append("")
        lines.append("Raw Intake")
        for index, item in enumerate(raw_items, start=1):
            venue = item.journal_name or item.source
            lines.append(f"{index}. {item.title_en} | {venue}")

    if result.missing_sources:
        lines.append("")
        lines.append(f"Missing sources: {', '.join(result.missing_sources)}")

    lines.append("")
    lines.append(f"Updated local dashboard: {result.artifacts.dashboard_page}")
    return "\n".join(lines).strip()


def build_weixin_brief_message_from_payload(
    payload: dict,
    *,
    max_curated_items: int = 5,
    max_raw_items: int = 3,
) -> str:
    lines: list[str] = []
    run_date = payload.get("run_date") or datetime.now(UTC).date().isoformat()
    lines.append(f"AI + Mental Health Daily Brief | {run_date}")
    lines.append("")

    for bullet in (payload.get("executive_summary", {}) or {}).get("en", [])[:3]:
        lines.append(f"- {bullet}")

    curated_items = list(payload.get("curated_items", []))[:max_curated_items]
    if curated_items:
        lines.append("")
        lines.append("Curated")
        for index, item in enumerate(curated_items, start=1):
            venue = item.get("journal_name") or item.get("source") or "Unknown"
            published = (item.get("published_at") or "")[:10]
            lines.append(f"{index}. {item.get('title_en', 'Untitled')} | {venue} | {published}")

    raw_items = list(payload.get("raw_intake_items", []))[:max_raw_items]
    if raw_items:
        lines.append("")
        lines.append("Raw Intake")
        for index, item in enumerate(raw_items, start=1):
            venue = item.get("journal_name") or item.get("source") or "Unknown"
            lines.append(f"{index}. {item.get('title_en', 'Untitled')} | {venue}")

    missing_sources = payload.get("missing_sources", []) or []
    if missing_sources:
        lines.append("")
        lines.append(f"Missing sources: {', '.join(missing_sources)}")

    dashboard_path = payload.get("dashboard_page") or payload.get("state", {}).get("dashboard_page")
    if dashboard_path:
        lines.append("")
        lines.append(f"Updated local dashboard: {dashboard_path}")
    return "\n".join(lines).strip()


def load_latest_brief_payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def is_payload_fresh_for_date(payload: dict, *, target_date: str) -> bool:
    return str(payload.get("run_date") or "") == target_date


def load_delivery_log(path: Path) -> dict:
    if not path.exists():
        return {"entries": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_delivery_log(path: Path, log: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def message_fingerprint(message: str) -> str:
    return sha1(message.encode("utf-8")).hexdigest()


def was_message_delivered(
    log: dict,
    *,
    run_date: str,
    message: str,
    peer_user_ids: list[str],
) -> bool:
    digest = message_fingerprint(message)
    delivered = {
        entry["peer_user_id"]
        for entry in log.get("entries", [])
        if entry.get("run_date") == run_date and entry.get("message_fingerprint") == digest
    }
    return all(user_id in delivered for user_id in peer_user_ids)


def record_delivery(
    path: Path,
    *,
    run_date: str,
    message: str,
    peer_user_ids: list[str],
    delivered_at: datetime | None = None,
) -> None:
    log = load_delivery_log(path)
    digest = message_fingerprint(message)
    timestamp = (delivered_at or datetime.now(UTC)).astimezone(UTC).isoformat()
    existing = [
        entry
        for entry in log.get("entries", [])
        if not (
            entry.get("run_date") == run_date
            and entry.get("message_fingerprint") == digest
            and entry.get("peer_user_id") in peer_user_ids
        )
    ]
    for user_id in peer_user_ids:
        existing.append(
            {
                "run_date": run_date,
                "peer_user_id": user_id,
                "message_fingerprint": digest,
                "delivered_at": timestamp,
            }
        )
    log["entries"] = existing[-200:]
    save_delivery_log(path, log)
