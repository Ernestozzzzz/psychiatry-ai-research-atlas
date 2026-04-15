from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from intel_center.admission import evaluate_candidates
from intel_center.config import load_project_config
from intel_center.fetchers import parse_rss_or_atom
from intel_center.models import CandidateDefinition, IntelItem, LLMOptions, SourceDefinition
from intel_center.pipeline import (
    _load_source_cache,
    _write_source_cache,
    run_morning_brief,
)


def test_load_project_config_has_expected_topics() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_project_config(root)
    assert "psychiatry_ai_frontier" in config.topics
    assert any(source.track == "frontier_core" for source in config.sources)
    assert any(candidate.candidate_id == "pubmed_europepmc" for candidate in config.candidates)


def test_candidate_evaluation_thresholds_and_blocking_rule() -> None:
    rubric = {
        "weights": {
            "provenance": 0.15,
            "transparency": 0.12,
            "safety": 0.14,
            "controllability": 0.1,
            "utility": 0.14,
            "signal_quality": 0.13,
            "maintainability": 0.1,
            "fit_for_ai_mental_health": 0.12,
        },
        "adopt_threshold": 80,
        "review_threshold": 60,
        "blocking_floor": 2,
    }
    candidates = [
        CandidateDefinition(
            candidate_id="good-source",
            candidate_type="database",
            name="Good Source",
            homepage_url="https://example.com",
            suitable_for=["source"],
            scores={key: 5 for key in rubric["weights"]},
        ),
        CandidateDefinition(
            candidate_id="unsafe-source",
            candidate_type="database",
            name="Unsafe Source",
            homepage_url="https://example.com",
            suitable_for=["source"],
            scores={
                "provenance": 5,
                "transparency": 5,
                "safety": 1,
                "controllability": 5,
                "utility": 5,
                "signal_quality": 5,
                "maintainability": 5,
                "fit_for_ai_mental_health": 5,
            },
        ),
    ]

    evaluations = {entry.candidate_id: entry for entry in evaluate_candidates(candidates, rubric)}
    assert evaluations["good-source"].admission_status == "adopt"
    assert evaluations["good-source"].implementation_recommendation == "直接接入"
    assert evaluations["unsafe-source"].admission_status == "review"


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
    assert len(result.selected_items) >= 5
    assert len(result.curated_items) >= 5
    assert any(item.curation_bucket == "raw_intake" for item in result.raw_intake_items)
    assert result.candidate_evaluations

    page_text = result.artifacts.dashboard_page.read_text(encoding="utf-8")
    data_text = result.artifacts.dashboard_data.read_text(encoding="utf-8")
    assert "Research Atlas" in page_text
    assert "English" in page_text
    assert "Load more" in page_text
    assert "High-signal" in page_text
    assert "Raw Intake" in page_text
    assert "Admission Report" in page_text
    assert "Market Context" not in page_text
    assert '"frontier_core"' in data_text
    assert '"initial_render_count": 16' in data_text
    assert '"months": [' in data_text
    assert '"month_count": 4' in data_text
    assert '"curated_items": [' in data_text
    assert '"raw_intake_items": [' in data_text
    assert '"candidate_report": {' in data_text


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


def test_archive_items_stay_in_raw_intake_unless_promoted(tmp_path: Path) -> None:
    root = _prepare_workspace(tmp_path)
    fixture = root / "tests" / "fixtures" / "mock_bundle.json"
    result = run_morning_brief(
        root=root,
        run_at=datetime(2026, 3, 23, 7, 30, tzinfo=UTC),
        fixture_bundle_path=fixture,
    )

    curated_titles = {item.title_en for item in result.curated_items}
    raw_titles = {item.title_en for item in result.raw_intake_items}
    assert "Agentic screening pipeline for suicide risk escalation from longitudinal mental health notes" not in curated_titles
    assert "Large language model prompt benchmark for anxiety journaling classification in digital mental health apps" not in curated_titles
    assert "Agentic screening pipeline for suicide risk escalation from longitudinal mental health notes" in raw_titles
    assert "Large language model prompt benchmark for anxiety journaling classification in digital mental health apps" in raw_titles


def test_pipeline_handles_missing_neuroblu_source(tmp_path: Path, monkeypatch) -> None:
    root = _prepare_workspace(tmp_path)
    fixture = root / "tests" / "fixtures" / "mock_bundle.json"

    from intel_center import pipeline as pipeline_module

    original_fetch = pipeline_module.fetch_source_items

    def fake_fetch(source, *args, **kwargs):
        if source.id == "neuroblu_publications":
            raise pipeline_module.FetchError("neuroblu_publications: unavailable")
        return original_fetch(source, *args, **kwargs)

    monkeypatch.setattr(pipeline_module, "fetch_source_items", fake_fetch)
    result = run_morning_brief(
        root=root,
        run_at=datetime(2026, 3, 23, 7, 30, tzinfo=UTC),
        fixture_bundle_path=fixture,
    )

    assert "NeuroBlu Publications" in result.missing_sources
    assert result.curated_items


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
    assert '"label_en": "December 2025"' in data_text
    assert '"curation_bucket": "raw_intake"' in data_text
    assert '"implementation_recommendation": "直接接入"' in data_text


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


def test_source_cache_roundtrip(tmp_path: Path) -> None:
    root = _prepare_workspace(tmp_path)
    source = SourceDefinition(
        id="cached-source",
        name="Cached Source",
        kind="rss",
        track="frontier_core",
        url="https://example.org/feed",
    )
    item = IntelItem(
        id="item-1",
        title_en="Cached item",
        source="Cached Source",
        url="https://example.org/item-1",
        published_at=datetime(2026, 4, 13, 8, 0, tzinfo=UTC),
        track="frontier_core",
    )

    _write_source_cache(root, source, [item], generated_at=datetime(2026, 4, 13, 9, 0, tzinfo=UTC))
    cached = _load_source_cache(
        root,
        source,
        generated_at=datetime(2026, 4, 13, 10, 0, tzinfo=UTC),
        max_age_seconds=6 * 60 * 60,
    )

    assert cached is not None
    assert cached[0].title_en == "Cached item"
    assert cached[0].published_at == datetime(2026, 4, 13, 8, 0, tzinfo=UTC)


def _prepare_workspace(tmp_path: Path) -> Path:
    source_root = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "workspace"
    shutil.copytree(source_root / "config", workspace / "config")
    shutil.copytree(source_root / "src", workspace / "src")
    shutil.copytree(source_root / "tests", workspace / "tests")
    (workspace / "intel").mkdir()
    (workspace / "state").mkdir()
    return workspace
