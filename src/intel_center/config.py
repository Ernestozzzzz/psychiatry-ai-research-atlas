from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .models import CandidateDefinition, SourceDefinition, TopicDefinition


@dataclass(slots=True)
class ProjectConfig:
    defaults: dict
    sources: list[SourceDefinition]
    candidates: list[CandidateDefinition]
    candidate_rubric: dict
    topics: dict[str, TopicDefinition]
    modality_keywords: dict[str, list[str]]
    study_type_keywords: dict[str, list[str]]
    journal_rankings: dict[str, dict[str, str]]
    market_indices: list[dict]
    market_watchlist: list[dict]


def _load_data_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        text = handle.read()
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text)
    except Exception:
        return json.loads(text)


def load_project_config(root: Path) -> ProjectConfig:
    sources_path = root / "config" / "sources.yml"
    candidates_path = root / "config" / "candidates.yml"
    topics_path = root / "config" / "topics.yml"
    journals_path = root / "config" / "journals.yml"
    sources_blob = _load_data_file(sources_path)
    candidates_blob = _load_data_file(candidates_path)
    topics_blob = _load_data_file(topics_path)
    journals_blob = _load_data_file(journals_path) if journals_path.exists() else {"journals": {}}

    sources: list[SourceDefinition] = []
    for track, track_cfg in sources_blob["tracks"].items():
        for raw in track_cfg["sources"]:
            sources.append(
                SourceDefinition(
                    id=raw["id"],
                    name=raw["name"],
                    kind=raw["kind"],
                    track=track,
                    priority=float(raw.get("priority", 0.5)),
                    url=raw.get("url"),
                    query=raw.get("query"),
                    page_size=int(raw.get("page_size", 20)),
                    max_pages=int(raw.get("max_pages", 1)),
                    tags=list(raw.get("tags", [])),
                    topic_keys=list(raw.get("topic_keys", [])),
                    source_class=raw.get("source_class", "database"),
                    admission_status=raw.get("admission_status", "adopt"),
                    evidence_level=raw.get("evidence_level", "peer_reviewed"),
                    curation_tier=raw.get("curation_tier", "curated"),
                    candidate_id=raw.get("candidate_id"),
                )
            )

    candidates: list[CandidateDefinition] = []
    for raw in candidates_blob["candidates"]:
        candidates.append(
            CandidateDefinition(
                candidate_id=raw["candidate_id"],
                candidate_type=raw["candidate_type"],
                name=raw["name"],
                homepage_url=raw["homepage_url"],
                source_repo_url=raw.get("source_repo_url"),
                official_docs_url=raw.get("official_docs_url"),
                execution_mode=raw.get("execution_mode", "hosted"),
                data_sources_declared=list(raw.get("data_sources_declared", [])),
                trust_notes=list(raw.get("trust_notes", [])),
                scope_tags=list(raw.get("scope_tags", [])),
                status=raw.get("status", "candidate"),
                last_reviewed_at=raw.get("last_reviewed_at"),
                scores=dict(raw.get("scores", {})),
                key_risks=list(raw.get("key_risks", [])),
                borrow_notes=list(raw.get("borrow_notes", [])),
                suitable_for=list(raw.get("suitable_for", [])),
                review_recommendation=raw.get("review_recommendation", ""),
            )
        )

    topics: dict[str, TopicDefinition] = {}
    for key, raw in topics_blob["topics"].items():
        topics[key] = TopicDefinition(
            key=key,
            label=raw["label"],
            zh_label=raw["zh_label"],
            weight=float(raw["weight"]),
            keywords=list(raw["keywords"]),
        )

    market_cfg = sources_blob.get("market_snapshots", {})
    return ProjectConfig(
        defaults=dict(sources_blob["defaults"]),
        sources=sources,
        candidates=candidates,
        candidate_rubric=dict(candidates_blob["rubric"]),
        topics=topics,
        modality_keywords=dict(topics_blob.get("modality_keywords", {})),
        study_type_keywords=dict(topics_blob.get("study_type_keywords", {})),
        journal_rankings=dict(journals_blob.get("journals", {})),
        market_indices=list(market_cfg.get("indices", [])),
        market_watchlist=list(market_cfg.get("watchlist", [])),
    )
