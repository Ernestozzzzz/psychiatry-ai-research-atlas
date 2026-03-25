from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from intel_center.config import load_project_config
from intel_center.fetchers import parse_rss_or_atom
from intel_center.models import LLMOptions, SourceDefinition
from intel_center.pipeline import run_morning_brief


def test_load_project_config_has_expected_topics() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_project_config(root)
    assert "psychiatry_ai_frontier" in config.topics
    assert any(source.track == "frontier_core" for source in config.sources)


def test_parse_atom_feed() -> None:
    source = SourceDefinition(
        id="test-atom",
        name="Test Atom",
        kind="atom",
        track="frontier_core",
        url="https://example.org/atom",
    )
    payload = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Clinical LLM benchmark for depression care</title>
    <link href="https://example.org/entry-1" />
    <updated>2026-03-23T08:00:00Z</updated>
    <summary>Benchmark study.</summary>
  </entry>
</feed>
"""
    items = parse_rss_or_atom(source, payload)
    assert len(items) == 1
    assert items[0].title_en == "Clinical LLM benchmark for depression care"
    assert items[0].url == "https://example.org/entry-1"


def test_pipeline_generates_dashboard(tmp_path: Path) -> None:
    root = _prepare_workspace(tmp_path)
    fixture = root / "tests" / "fixtures" / "mock_bundle.json"
    result = run_morning_brief(
        root=root,
        run_at=datetime(2026, 3, 23, 7, 30, tzinfo=UTC),
        fixture_bundle_path=fixture,
    )

    assert result.artifacts.dashboard_page.exists()
    assert result.artifacts.dashboard_data.exists()
    assert len(result.selected_items) >= 8

    page_text = result.artifacts.dashboard_page.read_text(encoding="utf-8")
    data_text = result.artifacts.dashboard_data.read_text(encoding="utf-8")
    assert "Research Atlas" in page_text
    assert "English" in page_text
    assert "Load more" in page_text
    assert "High-signal" in page_text
    assert "Market Context" not in page_text
    assert '"frontier_core"' in data_text
    assert '"initial_render_count": 16' in data_text
    assert '"months": [' in data_text
    assert '"month_count": 5' in data_text


def test_dashboard_filters_out_generic_ai_noise(tmp_path: Path) -> None:
    root = _prepare_workspace(tmp_path)
    fixture = root / "tests" / "fixtures" / "mock_bundle.json"
    result = run_morning_brief(
        root=root,
        run_at=datetime(2026, 3, 23, 7, 30, tzinfo=UTC),
        fixture_bundle_path=fixture,
    )
    titles = [item.title_en for item in result.selected_items]
    assert "WordPress.com now lets AI agents write and publish posts, and more" not in titles
    assert all("Nasdaq rises as AI megacaps lead premarket sentiment" != title for title in titles)
    assert "Transformer screening model for mood symptoms in online counseling platforms" not in titles


def test_dashboard_payload_supports_language_toggle_and_feed_cards(tmp_path: Path) -> None:
    root = _prepare_workspace(tmp_path)
    fixture = root / "tests" / "fixtures" / "mock_bundle.json"
    result = run_morning_brief(
        root=root,
        run_at=datetime(2026, 3, 23, 7, 30, tzinfo=UTC),
        fixture_bundle_path=fixture,
    )

    page_text = result.artifacts.dashboard_page.read_text(encoding="utf-8")
    data_text = result.artifacts.dashboard_data.read_text(encoding="utf-8")
    assert 'data-lang="zh"' in page_text
    assert "All months" in page_text
    assert "Best first" in page_text
    assert "last 12 months" in page_text
    assert '"label_en": "Clinical"' in data_text
    assert '"label_zh": "临床"' in data_text
    assert '"journal_name": "npj Digital Medicine"' in data_text
    assert '"journal_quartile": "Q1"' in data_text
    assert '"label_en": "November 2025"' in data_text


def test_pipeline_llm_mode_overrides_summary(tmp_path: Path) -> None:
    root = _prepare_workspace(tmp_path)
    fixture = root / "tests" / "fixtures" / "mock_bundle.json"

    def fake_requester(url: str, payload: dict, headers: dict) -> dict:
        assert payload["text"]["format"]["type"] == "json_object"
        return {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": """{
  "executive_summary_bullets": [
    "LLM summary 1",
    "LLM summary 2",
    "LLM summary 3"
  ]
}""",
                        }
                    ],
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        }

    result = run_morning_brief(
        root=root,
        run_at=datetime(2026, 3, 23, 7, 30, tzinfo=UTC),
        fixture_bundle_path=fixture,
        llm_options=LLMOptions(api_key="test-key"),
        llm_requester=fake_requester,
    )

    assert result.llm_enhancement is not None
    assert result.executive_summary_bullets == ["LLM summary 1", "LLM summary 2", "LLM summary 3"]

    page_text = result.artifacts.dashboard_page.read_text(encoding="utf-8")
    assert "LLM enhancement applied with `gpt-4o-mini`" in page_text
    assert "LLM summary 1" in page_text


def _prepare_workspace(tmp_path: Path) -> Path:
    source_root = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "workspace"
    shutil.copytree(source_root / "config", workspace / "config")
    shutil.copytree(source_root / "src", workspace / "src")
    shutil.copytree(source_root / "tests", workspace / "tests")
    (workspace / "intel").mkdir()
    (workspace / "state").mkdir()
    return workspace
