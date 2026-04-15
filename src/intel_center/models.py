from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class CandidateDefinition:
    candidate_id: str
    candidate_type: str
    name: str
    homepage_url: str
    source_repo_url: str | None = None
    official_docs_url: str | None = None
    execution_mode: str = "hosted"
    data_sources_declared: list[str] = field(default_factory=list)
    trust_notes: list[str] = field(default_factory=list)
    scope_tags: list[str] = field(default_factory=list)
    status: str = "candidate"
    last_reviewed_at: str | None = None
    scores: dict[str, float] = field(default_factory=dict)
    key_risks: list[str] = field(default_factory=list)
    borrow_notes: list[str] = field(default_factory=list)
    suitable_for: list[str] = field(default_factory=list)
    review_recommendation: str = ""


@dataclass(slots=True)
class CandidateEvaluation:
    candidate_id: str
    dimension_scores: dict[str, float]
    weighted_total: float
    admission_score: float
    admission_status: str
    implementation_recommendation: str
    key_risks: list[str] = field(default_factory=list)
    borrow_notes: list[str] = field(default_factory=list)
    suitable_for: list[str] = field(default_factory=list)
    review_recommendation: str = ""


@dataclass(slots=True)
class SourceDefinition:
    id: str
    name: str
    kind: str
    track: str
    priority: float = 0.5
    url: str | None = None
    query: str | None = None
    page_size: int = 20
    max_pages: int = 1
    tags: list[str] = field(default_factory=list)
    topic_keys: list[str] = field(default_factory=list)
    source_class: str = "database"
    admission_status: str = "adopt"
    evidence_level: str = "peer_reviewed"
    curation_tier: str = "curated"
    candidate_id: str | None = None


@dataclass(slots=True)
class TopicDefinition:
    key: str
    label: str
    zh_label: str
    weight: float
    keywords: list[str]


@dataclass(slots=True)
class IntelItem:
    id: str
    title_en: str
    source: str
    url: str
    published_at: datetime
    track: str
    tags: list[str] = field(default_factory=list)
    source_id: str | None = None
    summary_en: str = ""
    summary_zh: str = ""
    card_summary_en: str = ""
    card_summary_zh: str = ""
    why_important: str = ""
    why_important_zh: str = ""
    journal_name: str = ""
    journal_quartile: str = ""
    impact_factor: str = ""
    venue_type: str = ""
    quality_tier: str = ""
    market_symbols: list[str] = field(default_factory=list)
    matched_topics: list[str] = field(default_factory=list)
    matched_topics_zh: list[str] = field(default_factory=list)
    study_type: str = "unknown"
    modality: str = "unknown"
    clinical_relevance: float = 0.0
    idea_potential_score: float = 0.0
    importance_score: float = 0.0
    item_importance_score: float = 0.0
    admission_score: float = 0.0
    evidence_level: str = "unknown"
    source_class: str = "database"
    source_admission_status: str = "candidate"
    curation_tier: str = "curated"
    curation_bucket: str = "raw_intake"
    screening_notes: list[str] = field(default_factory=list)
    is_archive: bool = False
    is_top_journal: bool = False
    corroborating_sources: list[str] = field(default_factory=list)


@dataclass(slots=True)
class IdeaCandidate:
    idea_title: str
    why_now: str
    seed_evidence: list[str]
    possible_dataset_or_signal: str
    candidate_method: str
    paper_or_project_shape: str
    score: float
    source_item_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MarketSnapshot:
    symbol: str
    close: float | None
    change_pct: float | None
    as_of: str | None
    status: str


@dataclass(slots=True)
class RunArtifacts:
    dashboard_page: Path
    dashboard_data: Path
    state_file: Path


@dataclass(slots=True)
class LLMOptions:
    api_key: str
    model: str = "gpt-4o-mini"
    base_url: str = "https://api.openai.com/v1/responses"
    max_output_tokens: int = 1800


@dataclass(slots=True)
class LLMEnhancement:
    executive_summary_bullets: list[str]
    model: str
    usage: dict[str, int] | None = None


@dataclass(slots=True)
class RunResult:
    generated_at: datetime
    artifacts: RunArtifacts
    selected_items: list[IntelItem]
    curated_items: list[IntelItem]
    raw_intake_items: list[IntelItem]
    missing_sources: list[str]
    candidate_evaluations: list[CandidateEvaluation] = field(default_factory=list)
    executive_summary_bullets: list[str] = field(default_factory=list)
    llm_enhancement: LLMEnhancement | None = None
    llm_error: str | None = None
