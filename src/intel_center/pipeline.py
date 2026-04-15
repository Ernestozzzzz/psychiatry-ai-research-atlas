from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha1
from pathlib import Path

from .admission import evaluate_candidates
from .config import load_project_config
from .fetchers import FetchError, fetch_source_items
from .llm import LLMEnhancementError, generate_llm_enhancement
from .models import IntelItem, LLMOptions, RunArtifacts, RunResult
from .render import build_dashboard_payload, build_executive_bullets, render_dashboard_html
from .scoring import dedupe_items, enrich_and_score_items, split_curated_and_raw_items

SOURCE_CACHE_TTL_SECONDS = 6 * 60 * 60
SOURCE_CACHE_STALE_FALLBACK_SECONDS = 24 * 60 * 60


def run_morning_brief(
    *,
    root: Path,
    run_at: datetime | None = None,
    dry_run: bool = False,
    open_note: bool = False,
    fixture_bundle_path: Path | None = None,
    llm_options: LLMOptions | None = None,
    llm_requester=None,
    source_fetcher=None,
) -> RunResult:
    root = root.resolve()
    config = load_project_config(root)
    generated_at = (run_at or datetime.now(UTC)).astimezone(UTC)
    run_date = generated_at.date().isoformat()
    resolved_source_fetcher = source_fetcher or fetch_source_items
    fixture_bundle = _load_fixture_bundle(fixture_bundle_path) if fixture_bundle_path else None
    date_cutoff = generated_at - timedelta(days=int(config.defaults["days_back"]))
    candidate_evaluations = evaluate_candidates(config.candidates, config.candidate_rubric)
    candidate_lookup = {entry.candidate_id: entry for entry in candidate_evaluations}

    items = []
    missing_sources: list[str] = []
    active_sources = _active_sources(config.sources, candidate_lookup)
    parallel_sources = [source for source in active_sources if source.kind != "pubmed"]
    serial_sources = [source for source in active_sources if source.kind == "pubmed"]

    if parallel_sources:
        max_workers = min(8, max(1, len(parallel_sources)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    _fetch_source_with_cache,
                    root,
                    source,
                    resolved_source_fetcher,
                    fixture_bundle,
                    date_cutoff,
                    generated_at,
                ): source
                for source in parallel_sources
            }
            for future in as_completed(future_map):
                source = future_map[future]
                try:
                    fetched = future.result()
                    for item in fetched:
                        evaluation = candidate_lookup.get(source.candidate_id or "")
                        item.admission_score = evaluation.admission_score if evaluation else 0.0
                        item.source_admission_status = source.admission_status
                    items.extend(fetched)
                except FetchError:
                    missing_sources.append(source.name)

    for source in serial_sources:
        try:
            fetched = _fetch_source_with_cache(
                root,
                source,
                resolved_source_fetcher,
                fixture_bundle,
                date_cutoff,
                generated_at,
            )
            for item in fetched:
                evaluation = candidate_lookup.get(source.candidate_id or "")
                item.admission_score = evaluation.admission_score if evaluation else 0.0
                item.source_admission_status = source.admission_status
            items.extend(fetched)
        except FetchError:
            missing_sources.append(source.name)

    enriched = enrich_and_score_items(items, config, generated_at)
    deduped = dedupe_items(enriched)
    curated, raw_intake = split_curated_and_raw_items(
        deduped,
        config,
        top_n=int(config.defaults["top_n"]),
        min_primary_research_items=int(config.defaults["min_primary_research_items"]),
        max_macro_items=int(config.defaults["max_macro_items"]),
        max_preprint_items=int(config.defaults.get("max_preprint_items", 72)),
        max_items_per_source=int(config.defaults.get("max_items_per_source", 18)),
        raw_intake_top_n=int(config.defaults.get("raw_intake_top_n", config.defaults["top_n"])),
    )
    executive_summary = build_executive_bullets(
        [*curated, *raw_intake],
        missing_sources,
        days_back=int(config.defaults["days_back"]),
    )
    llm_enhancement = None
    llm_error = None
    if llm_options:
        try:
            llm_enhancement = generate_llm_enhancement(
                run_date=run_date,
                items=curated,
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
        curated_items=curated,
        raw_intake_items=raw_intake,
        missing_sources=missing_sources,
        executive_summary=executive_summary,
        initial_render_count=int(config.defaults.get("initial_render_count", 12)),
        llm_note=llm_note,
        candidate_evaluations=_serialize_candidate_report(config, candidate_lookup),
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
        selected_items=curated,
        curated_items=curated,
        raw_intake_items=raw_intake,
        missing_sources=missing_sources,
        candidate_evaluations=candidate_evaluations,
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


def _active_sources(sources, candidate_lookup):
    active = []
    for source in sources:
        evaluation = candidate_lookup.get(source.candidate_id or "")
        if evaluation and evaluation.admission_status == "reject":
            continue
        active.append(source)
    return active


def _serialize_candidate_report(config, candidate_lookup) -> dict:
    entries = []
    for candidate in config.candidates:
        evaluation = candidate_lookup[candidate.candidate_id]
        entries.append(
            {
                "candidate_id": candidate.candidate_id,
                "candidate_type": candidate.candidate_type,
                "name": candidate.name,
                "homepage_url": candidate.homepage_url,
                "source_repo_url": candidate.source_repo_url,
                "official_docs_url": candidate.official_docs_url,
                "execution_mode": candidate.execution_mode,
                "data_sources_declared": candidate.data_sources_declared,
                "trust_notes": candidate.trust_notes,
                "scope_tags": candidate.scope_tags,
                "status": evaluation.admission_status,
                "last_reviewed_at": candidate.last_reviewed_at,
                "dimension_scores": evaluation.dimension_scores,
                "admission_score": evaluation.admission_score,
                "implementation_recommendation": evaluation.implementation_recommendation,
                "key_risks": evaluation.key_risks,
                "borrow_notes": evaluation.borrow_notes,
                "suitable_for": evaluation.suitable_for,
                "review_recommendation": evaluation.review_recommendation,
            }
        )
    counts = {
        "adopt": sum(1 for entry in entries if entry["status"] == "adopt"),
        "review": sum(1 for entry in entries if entry["status"] == "review"),
        "reject": sum(1 for entry in entries if entry["status"] == "reject"),
    }
    return {"summary": counts, "entries": entries}


def _fetch_source_with_cache(
    root: Path,
    source,
    source_fetcher,
    fixture_bundle,
    date_cutoff,
    generated_at: datetime,
):
    if fixture_bundle is not None:
        return source_fetcher(source, fixture_bundle=fixture_bundle, date_cutoff=date_cutoff)

    cached_items = _load_source_cache(root, source, generated_at=generated_at, max_age_seconds=SOURCE_CACHE_TTL_SECONDS)
    if cached_items is not None:
        return _filter_items_by_cutoff(cached_items, date_cutoff)

    try:
        fresh_items = source_fetcher(source, fixture_bundle=None, date_cutoff=date_cutoff)
        _write_source_cache(root, source, fresh_items, generated_at=generated_at)
        return fresh_items
    except FetchError:
        fallback_items = _load_source_cache(
            root,
            source,
            generated_at=generated_at,
            max_age_seconds=SOURCE_CACHE_STALE_FALLBACK_SECONDS,
        )
        if fallback_items is not None:
            for item in fallback_items:
                if "Served from recent source cache after fetch failure." not in item.screening_notes:
                    item.screening_notes.append("Served from recent source cache after fetch failure.")
            return _filter_items_by_cutoff(fallback_items, date_cutoff)
        raise


def _source_cache_path(root: Path, source) -> Path:
    digest = sha1(_source_signature(source).encode("utf-8")).hexdigest()[:12]
    return root / "state" / "source_cache" / f"{source.id}-{digest}.json"


def _source_signature(source) -> str:
    return json.dumps(
        {
            "id": source.id,
            "kind": source.kind,
            "url": source.url,
            "query": source.query,
            "page_size": source.page_size,
            "max_pages": source.max_pages,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _write_source_cache(root: Path, source, items: list[IntelItem], *, generated_at: datetime) -> None:
    path = _source_cache_path(root, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_id": source.id,
        "signature": _source_signature(source),
        "generated_at": generated_at.isoformat(),
        "items": [_serialize_cached_item(item) for item in items],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_source_cache(root: Path, source, *, generated_at: datetime, max_age_seconds: int) -> list[IntelItem] | None:
    path = _source_cache_path(root, source)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if payload.get("signature") != _source_signature(source):
        return None
    generated_raw = payload.get("generated_at")
    if not generated_raw:
        return None
    cached_at = datetime.fromisoformat(generated_raw)
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=UTC)
    if (generated_at - cached_at.astimezone(UTC)).total_seconds() > max_age_seconds:
        return None
    return [_deserialize_cached_item(entry) for entry in payload.get("items", [])]


def _serialize_cached_item(item: IntelItem) -> dict:
    payload = asdict(item)
    payload["published_at"] = item.published_at.isoformat()
    return payload


def _deserialize_cached_item(payload: dict) -> IntelItem:
    materialized = dict(payload)
    materialized["published_at"] = datetime.fromisoformat(materialized["published_at"]).astimezone(UTC)
    return IntelItem(**materialized)


def _filter_items_by_cutoff(items: list[IntelItem], date_cutoff: datetime | None) -> list[IntelItem]:
    if date_cutoff is None:
        return items
    return [item for item in items if item.published_at >= date_cutoff]
