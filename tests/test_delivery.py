from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from intel_center.delivery import (
    build_weixin_brief_message,
    build_weixin_brief_message_from_payload,
    is_payload_fresh_for_date,
    load_latest_brief_payload,
    load_delivery_log,
    record_delivery,
    was_message_delivered,
)
from intel_center.models import IntelItem, RunArtifacts, RunResult


def test_build_weixin_brief_message_includes_curated_and_raw_sections(tmp_path: Path) -> None:
    now = datetime(2026, 4, 13, tzinfo=UTC)
    curated = IntelItem(
        id="1",
        title_en="AI triage model for youth depression",
        source="PubMed",
        url="https://example.com/1",
        published_at=now,
        track="current_program",
        journal_name="JAMA Psychiatry",
    )
    raw = IntelItem(
        id="2",
        title_en="Preprint on multimodal psychiatry foundation models",
        source="arXiv",
        url="https://example.com/2",
        published_at=now,
        track="frontier_core",
    )
    result = RunResult(
        generated_at=now,
        artifacts=RunArtifacts(
            dashboard_page=tmp_path / "index.html",
            dashboard_data=tmp_path / "latest.json",
            state_file=tmp_path / "state.json",
        ),
        selected_items=[curated],
        curated_items=[curated],
        raw_intake_items=[raw],
        missing_sources=["NeuroBlu publications"],
        executive_summary_bullets=[
            "High-signal psychiatry + AI work remains concentrated in peer-reviewed journals.",
            "Archive intake is active but still promotion-gated.",
        ],
    )

    message = build_weixin_brief_message(result, max_curated_items=1, max_raw_items=1)

    assert "AI + Mental Health Daily Brief | 2026-04-13" in message
    assert "Curated" in message
    assert "1. AI triage model for youth depression | JAMA Psychiatry | 2026-04-13" in message
    assert "Raw Intake" in message
    assert "1. Preprint on multimodal psychiatry foundation models | arXiv" in message
    assert "Missing sources: NeuroBlu publications" in message


def test_build_weixin_brief_message_from_payload_uses_cached_dashboard_data(tmp_path: Path) -> None:
    payload = {
        "run_date": "2026-04-13",
        "dashboard_page": str(tmp_path / "index.html"),
        "executive_summary": {"en": ["Cached summary 1", "Cached summary 2"]},
        "curated_items": [
            {
                "title_en": "Cached curated paper",
                "journal_name": "World Psychiatry",
                "source": "PubMed",
                "published_at": "2026-04-13T08:00:00+00:00",
            }
        ],
        "raw_intake_items": [
            {
                "title_en": "Cached raw paper",
                "source": "arXiv",
                "published_at": "2026-04-13T08:00:00+00:00",
            }
        ],
        "missing_sources": [],
    }

    message = build_weixin_brief_message_from_payload(payload, max_curated_items=1, max_raw_items=1)

    assert "Cached summary 1" in message
    assert "1. Cached curated paper | World Psychiatry | 2026-04-13" in message
    assert "1. Cached raw paper | arXiv" in message
    assert f"Updated local dashboard: {tmp_path / 'index.html'}" in message


def test_load_latest_brief_payload_and_freshness(tmp_path: Path) -> None:
    path = tmp_path / "latest_run.json"
    path.write_text(json.dumps({"run_date": "2026-04-13"}), encoding="utf-8")

    payload = load_latest_brief_payload(path)

    assert is_payload_fresh_for_date(payload, target_date="2026-04-13") is True
    assert is_payload_fresh_for_date(payload, target_date="2026-04-12") is False


def test_delivery_log_detects_duplicate_message(tmp_path: Path) -> None:
    log_path = tmp_path / "delivery.json"
    record_delivery(
        log_path,
        run_date="2026-04-13",
        message="brief payload",
        peer_user_ids=["alice@im.wechat"],
    )

    log = load_delivery_log(log_path)

    assert was_message_delivered(
        log,
        run_date="2026-04-13",
        message="brief payload",
        peer_user_ids=["alice@im.wechat"],
    ) is True
    assert was_message_delivered(
        log,
        run_date="2026-04-13",
        message="different payload",
        peer_user_ids=["alice@im.wechat"],
    ) is False
