from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import load_project_config
from .fetchers import FetchError, fetch_source_items
from .llm import LLMEnhancementError, generate_llm_enhancement
from .models import LLMOptions, RunArtifacts, RunResult
from .render import build_dashboard_payload, build_executive_bullets, render_dashboard_html
from .scoring import dedupe_items, enrich_and_score_items, filter_dashboard_items, select_top_items


def run_morning_brief(
    *,
    root: Path,
    run_at: datetime | None = None,
    dry_run: bool = False,
    open_note: bool = False,
    fixture_bundle_path: Path | None = None,
    llm_options: LLMOptions | None = None,
    llm_requester=None,
) -> RunResult:
    root = root.resolve()
    config = load_project_config(root)
    generated_at = (run_at or datetime.now(UTC)).astimezone(UTC)
    run_date = generated_at.date().isoformat()
    fixture_bundle = _load_fixture_bundle(fixture_bundle_path) if fixture_bundle_path else None
    date_cutoff = generated_at - timedelta(days=int(config.defaults["days_back"]))

    items = []
    missing_sources: list[str] = []
    for source in config.sources:
        try:
            items.extend(fetch_source_items(source, fixture_bundle=fixture_bundle, date_cutoff=date_cutoff))
        except FetchError:
            missing_sources.append(source.name)

    enriched = enrich_and_score_items(items, config, generated_at)
    deduped = dedupe_items(enriched)
    filtered = filter_dashboard_items(deduped)
    selected = select_top_items(
        filtered,
        top_n=int(config.defaults["top_n"]),
        min_primary_research_items=int(config.defaults["min_primary_research_items"]),
        max_macro_items=int(config.defaults["max_macro_items"]),
        max_preprint_items=int(config.defaults.get("max_preprint_items", 72)),
        max_items_per_source=int(config.defaults.get("max_items_per_source", 18)),
    )
    executive_summary = build_executive_bullets(
        selected,
        missing_sources,
        days_back=int(config.defaults["days_back"]),
    )
    llm_enhancement = None
    llm_error = None
    if llm_options:
        try:
            llm_enhancement = generate_llm_enhancement(
                run_date=run_date,
                items=selected,
                options=llm_options,
                requester=llm_requester,
            )
            executive_summary["en"] = llm_enhancement.executive_summary_bullets
        except LLMEnhancementError as exc:
            llm_error = str(exc)

    artifacts = _build_artifacts(root, run_date)
    llm_note = _llm_note(llm_enhancement, llm_error)
    payload = build_dashboard_payload(
        run_date=run_date,
        generated_at=generated_at,
        days_back=int(config.defaults["days_back"]),
        items=selected,
        missing_sources=missing_sources,
        executive_summary=executive_summary,
        initial_render_count=int(config.defaults.get("initial_render_count", 12)),
        llm_note=llm_note,
    )
    html = render_dashboard_html(payload)
    payload_json = json.dumps(payload, ensure_ascii=False, indent=2)

    if dry_run:
        sys.stdout.write(html)
        sys.stdout.write("\n")
    else:
        artifacts.dashboard_page.parent.mkdir(parents=True, exist_ok=True)
        artifacts.dashboard_data.parent.mkdir(parents=True, exist_ok=True)
        artifacts.dashboard_page.write_text(html, encoding="utf-8")
        artifacts.dashboard_data.write_text(payload_json, encoding="utf-8")
        _write_state_file(
            artifacts.state_file,
            payload,
            llm_enhancement=llm_enhancement,
            llm_error=llm_error,
        )
        if open_note:
            _open_note(artifacts.dashboard_page)

    return RunResult(
        generated_at=generated_at,
        artifacts=artifacts,
        selected_items=selected,
        missing_sources=missing_sources,
        executive_summary_bullets=executive_summary["en"],
        llm_enhancement=llm_enhancement,
        llm_error=llm_error,
    )


def _build_artifacts(root: Path, run_date: str) -> RunArtifacts:
    return RunArtifacts(
        dashboard_page=root / "intel" / "site" / "index.html",
        dashboard_data=root / "intel" / "site" / "latest.json",
        state_file=root / "state" / "latest_run.json",
    )


def _load_fixture_bundle(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_state_file(
    path: Path,
    payload: dict,
    *,
    llm_enhancement=None,
    llm_error: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state_payload = {
        **payload,
        "llm": {
            "applied": llm_enhancement is not None,
            "model": llm_enhancement.model if llm_enhancement else None,
            "usage": llm_enhancement.usage if llm_enhancement else None,
            "error": llm_error,
        },
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state_payload, handle, ensure_ascii=False, indent=2)


def _open_note(path: Path) -> None:
    if sys.platform != "darwin":
        return
    subprocess.run(["open", str(path)], check=False)


def _llm_note(llm_enhancement, llm_error: str | None) -> str | None:
    if llm_enhancement:
        usage = llm_enhancement.usage or {}
        if usage:
            return (
                f"LLM enhancement applied with `{llm_enhancement.model}` "
                f"({usage.get('input_tokens', 0)} input / {usage.get('output_tokens', 0)} output tokens)."
            )
        return f"LLM enhancement applied with `{llm_enhancement.model}`."
    if llm_error:
        return f"LLM enhancement requested but fell back to local heuristics: {llm_error}"
    return None
