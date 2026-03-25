from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .models import SourceDefinition, TopicDefinition


@dataclass(slots=True)
class ProjectConfig:
    defaults: dict
    sources: list[SourceDefinition]
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
    topics_path = root / "config" / "topics.yml"
    journals_path = root / "config" / "journals.yml"
    sources_blob = _load_data_file(sources_path)
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
        topics=topics,
        modality_keywords=dict(topics_blob.get("modality_keywords", {})),
        study_type_keywords=dict(topics_blob.get("study_type_keywords", {})),
        journal_rankings=dict(journals_blob.get("journals", {})),
        market_indices=list(market_cfg.get("indices", [])),
        market_watchlist=list(market_cfg.get("watchlist", [])),
    )
