from __future__ import annotations

import re
from datetime import UTC, datetime
from difflib import SequenceMatcher
from urllib.parse import urlparse

from .config import ProjectConfig
from .models import IntelItem

TRACK_BONUS = {
    "frontier_core": 42.0,
    "current_program": 34.0,
    "enabling_signals": 18.0,
}

PSYCH_TERMS = [
    "psychiatry",
    "psychiatric",
    "psychology",
    "psychological",
    "psychotherapy",
    "mental health",
    "mental illness",
    "depression",
    "depressive",
    "anxiety",
    "suicide",
    "suicidality",
    "psychosis",
    "bipolar",
    "schizophrenia",
    "mood disorder",
    "behavioral",
    "behavioural",
    "cognitive",
]

CORE_PSYCH_TERMS = [
    "psychiatry",
    "psychiatric",
    "mental health",
    "depression",
    "depressive",
    "anxiety",
    "suicide",
    "suicidality",
    "psychosis",
    "bipolar",
    "schizophrenia",
    "psychotherapy",
    "ptsd",
    "obsessive compulsive",
    "ocd",
    "eating disorder",
    "self-harm",
    "mood disorder",
]

CLINICAL_FOCUS_TERMS = [
    "patient",
    "clinical",
    "cohort",
    "screening",
    "diagnosis",
    "treatment",
    "intervention",
    "psychotherapy",
    "monitoring",
    "prediction",
    "risk",
    "trajectory",
    "longitudinal",
    "comorbidity",
    "multimorbidity",
    "integrated care",
    "digital phenotyping",
    "passive sensing",
    "wearable",
    "smartphone",
    "ecological momentary assessment",
]

AI_TERMS = [
    "ai-enabled",
    "artificial intelligence",
    "machine learning",
    "deep learning",
    "large language model",
    "llm",
    "foundation model",
    "agent",
    "agentic",
    "multimodal",
    "neural",
    "representation learning",
]

COMPUTATIONAL_SIGNAL_TERMS = [
    *AI_TERMS,
    "digital phenotyping",
    "passive sensing",
    "wearable",
    "smartphone",
    "actigraphy",
    "ecological momentary assessment",
    "computational",
    "voice biomarker",
    "speech biomarker",
    "risk model",
    "prediction model",
]

INSTITUTION_TERMS = [
    "guideline",
    "regulation",
    "policy",
    "framework",
    "clinical workflow",
    "validation",
    "documentation",
    "decision support",
    "fda",
    "nih",
    "nimh",
]

NEGATIVE_FRONTIER_TERMS = [
    "maternal mortality",
    "covid surveillance",
    "outbreak",
    "gen-ai texts",
    "airport",
    "immigration",
    "sports",
]

LOW_SIGNAL_JOURNAL_TERMS = [
    "cureus",
]

TRUSTED_DOMAINS = {
    "pubmed.ncbi.nlm.nih.gov",
    "doi.org",
    "jamanetwork.com",
    "thelancet.com",
    "cambridge.org",
    "nature.com",
    "springer.com",
    "sciencedirect.com",
    "nejm.org",
    "oup.com",
    "bmj.com",
    "jmir.org",
    "nih.gov",
    "nimh.nih.gov",
    "fda.gov",
    "who.int",
    "arxiv.org",
    "osf.io",
    "psychiatryonline.org",
    "wiley.com",
    "onlinelibrary.wiley.com",
    "sagepub.com",
    "biologicalpsychiatryjournal.com",
    "mitpressjournals.org",
}


def enrich_and_score_items(items: list[IntelItem], config: ProjectConfig, now: datetime) -> list[IntelItem]:
    days_back = int(config.defaults.get("days_back", 30))
    for item in items:
        text = f"{item.title_en} {item.summary_en}".lower()
        matched_topics: list[str] = []
        matched_topics_zh: list[str] = []
        relevance = 0.0
        for key, topic in config.topics.items():
            hits = sum(1 for keyword in topic.keywords if keyword.lower() in text)
            if hits:
                matched_topics.append(topic.label)
                matched_topics_zh.append(topic.zh_label)
                relevance += hits * topic.weight

        inferred_modality = _infer_bucket(text, config.modality_keywords, default="general")
        inferred_study_type = _infer_bucket(text, config.study_type_keywords, default="study")
        recency = _recency_score(item.published_at, now, days_back=days_back)
        clinical_relevance = _clinical_relevance(text)
        novelty = _novelty_score(text)
        source_priority = item.track in {"frontier_core", "current_program"}
        _apply_journal_metadata(item, config)
        item.quality_tier = _quality_tier(item)
        venue_bonus = _venue_bonus(item)
        item.evidence_level = _item_evidence_level(item)

        item.matched_topics = matched_topics
        item.matched_topics_zh = matched_topics_zh
        item.modality = inferred_modality
        item.study_type = inferred_study_type
        item.clinical_relevance = round(clinical_relevance, 3)
        item.idea_potential_score = round(
            relevance * 1.2 + novelty * 5 + clinical_relevance * 4 + (4 if source_priority else 0),
            3,
        )
        item.importance_score = round(
            TRACK_BONUS.get(item.track, 0)
            + relevance * 8.5
            + recency * 11
            + novelty * 7
            + clinical_relevance * 11,
            3,
        )
        item.importance_score = round(
            item.importance_score + venue_bonus,
            3,
        )
        item.item_importance_score = item.importance_score
        item.card_summary_en = _build_card_summary_en(item)
        item.card_summary_zh = _build_card_summary_zh(item)
        item.summary_zh = item.card_summary_zh
        item.why_important = _build_importance_reason_en(item)
        item.why_important_zh = _build_importance_reason_zh(item)
    return items


def dedupe_items(items: list[IntelItem]) -> list[IntelItem]:
    by_url: dict[str, IntelItem] = {}
    for item in sorted(items, key=lambda entry: entry.importance_score, reverse=True):
        key = _normalize_url(item.url)
        existing = by_url.get(key)
        if existing is None:
            by_url[key] = item
            continue
        existing.corroborating_sources.append(item.source)

    deduped = list(by_url.values())
    deduped.sort(key=lambda entry: entry.importance_score, reverse=True)

    filtered: list[IntelItem] = []
    for candidate in deduped:
        duplicate = None
        candidate_text = f"{candidate.title_en} {candidate.summary_en}".lower()
        for existing in filtered:
            existing_text = f"{existing.title_en} {existing.summary_en}".lower()
            similarity = SequenceMatcher(None, candidate_text[:240], existing_text[:240]).ratio()
            if similarity >= 0.86:
                duplicate = existing
                break
        if duplicate:
            duplicate.corroborating_sources.extend([candidate.source, *candidate.corroborating_sources])
            continue
        filtered.append(candidate)
    return filtered


def filter_dashboard_items(items: list[IntelItem]) -> list[IntelItem]:
    filtered = [item for item in items if _is_dashboard_candidate(item) and _passes_quality_gate(item)]
    filtered.sort(key=lambda entry: (entry.importance_score, entry.published_at), reverse=True)
    return filtered


def split_curated_and_raw_items(
    items: list[IntelItem],
    config: ProjectConfig,
    *,
    top_n: int,
    min_primary_research_items: int,
    max_macro_items: int,
    max_preprint_items: int = 72,
    max_items_per_source: int = 18,
    raw_intake_top_n: int = 240,
) -> tuple[list[IntelItem], list[IntelItem]]:
    candidates = filter_dashboard_items(items)
    curated_pool = [item for item in candidates if _passes_curated_gate(item, config)]
    curated = select_top_items(
        curated_pool,
        top_n=top_n,
        min_primary_research_items=min_primary_research_items,
        max_macro_items=max_macro_items,
        max_preprint_items=max_preprint_items,
        max_items_per_source=max_items_per_source,
    )
    curated_ids = {item.id for item in curated}
    for item in curated:
        item.curation_bucket = "curated"

    raw = [item for item in candidates if item.id not in curated_ids]
    for item in raw:
        item.curation_bucket = "raw_intake"
    raw.sort(key=lambda entry: (entry.published_at, entry.importance_score), reverse=True)
    return curated, raw[:raw_intake_top_n]


def select_top_items(
    items: list[IntelItem],
    *,
    top_n: int,
    min_primary_research_items: int,
    max_macro_items: int,
    max_preprint_items: int = 72,
    max_items_per_source: int = 18,
) -> list[IntelItem]:
    del max_macro_items

    primary = [item for item in items if item.track in {"frontier_core", "current_program"}]
    enabling = [item for item in items if item.track == "enabling_signals"]

    selected: list[IntelItem] = []
    preprint_count = 0
    source_counts: dict[str, int] = {}
    for item in primary[:min_primary_research_items]:
        if item.venue_type == "preprint" and preprint_count >= max_preprint_items:
            continue
        if source_counts.get(item.source, 0) >= max_items_per_source:
            continue
        selected.append(item)
        if item.venue_type == "preprint":
            preprint_count += 1
        source_counts[item.source] = source_counts.get(item.source, 0) + 1

    for pool in (primary, enabling):
        for item in pool:
            if item in selected:
                continue
            if len(selected) >= top_n:
                break
            if item.venue_type == "preprint" and preprint_count >= max_preprint_items:
                continue
            if source_counts.get(item.source, 0) >= max_items_per_source:
                continue
            selected.append(item)
            if item.venue_type == "preprint":
                preprint_count += 1
            source_counts[item.source] = source_counts.get(item.source, 0) + 1
        if len(selected) >= top_n:
            break

    selected.sort(key=lambda entry: (entry.importance_score, entry.published_at), reverse=True)
    return selected[:top_n]


def _infer_bucket(text: str, keyword_map: dict[str, list[str]], default: str) -> str:
    best_bucket = default
    best_hits = 0
    for bucket, keywords in keyword_map.items():
        hits = sum(1 for keyword in keywords if keyword.lower() in text)
        if hits > best_hits:
            best_hits = hits
            best_bucket = bucket
    return best_bucket


def _recency_score(published_at: datetime, now: datetime, *, days_back: int) -> float:
    delta_hours = max((now - published_at.astimezone(UTC)).total_seconds() / 3600, 0.0)
    decay_hours = max(days_back * 24, 24)
    return max(0.0, 1.0 - min(delta_hours, decay_hours) / decay_hours)


def _clinical_relevance(text: str) -> float:
    hits = sum(1 for term in CLINICAL_FOCUS_TERMS if term in text)
    return min(1.0, hits / 6)


def _novelty_score(text: str) -> float:
    novelty_terms = [
        "multimodal",
        "foundation model",
        "large language model",
        "agent",
        "wearable",
        "digital phenotyping",
        "representation learning",
        "trajectory",
        "passive sensing",
    ]
    hits = sum(1 for term in novelty_terms if term in text)
    return min(1.0, hits / 4)


def _build_card_summary_en(item: IntelItem) -> str:
    condensed = _condense_summary(item.summary_en)
    if condensed:
        return condensed
    topics = ", ".join(item.matched_topics[:2]) or "psychiatry and psychology AI"
    return f"A {item.study_type} / {item.modality} item at the {topics} boundary."


def _build_card_summary_zh(item: IntelItem) -> str:
    condensed = _condense_summary(item.summary_en, limit=78)
    if condensed:
        return f"摘要版：{condensed}"
    topics = "、".join(item.matched_topics_zh[:2]) or "精神健康与 AI"
    if item.track == "frontier_core":
        prefix = "前沿信号"
    elif item.track == "current_program":
        prefix = "临床研究信号"
    else:
        prefix = "配套生态信号"
    return (
        f"{prefix}，主题集中在{topics}。"
        f"从公开摘要看，这更像一条 {item.study_type} / {item.modality} 条目，适合继续查看原文方法、样本和可迁移性。"
    )


def _build_importance_reason_en(item: IntelItem) -> str:
    signals: list[str] = []
    if item.venue_type == "journal":
        signals.append("peer-reviewed venue")
    elif item.venue_type == "preprint":
        signals.append("preprint")
    if item.is_top_journal:
        signals.append("top journal whitelist")
    if item.journal_quartile:
        signals.append(item.journal_quartile)
    if item.impact_factor:
        signals.append(f"IF {item.impact_factor}")
    signals.append(f"{item.study_type} / {item.modality}")
    return " · ".join(signals[:4])


def _build_importance_reason_zh(item: IntelItem) -> str:
    signals: list[str] = []
    if item.venue_type == "journal":
        signals.append("同行评议期刊")
    elif item.venue_type == "preprint":
        signals.append("预印本")
    if item.is_top_journal:
        signals.append("顶级期刊白名单")
    if item.journal_quartile:
        signals.append(item.journal_quartile)
    if item.impact_factor:
        signals.append(f"IF {item.impact_factor}")
    signals.append(f"{item.study_type} / {item.modality}")
    return " · ".join(signals[:4])


def _is_dashboard_candidate(item: IntelItem) -> bool:
    text = f"{item.title_en} {item.summary_en}".lower()
    title_text = item.title_en.lower()
    has_core_psych = _has_any(text, CORE_PSYCH_TERMS)
    has_ai = _has_any(text, AI_TERMS)
    has_computational_signal = _has_any(text, COMPUTATIONAL_SIGNAL_TERMS)
    has_institution_signal = _has_any(text, INSTITUTION_TERMS)
    has_comorbidity = "comorbidity" in text or "multimorbidity" in text or "integrated care" in text
    title_has_core_psych = _has_any(title_text, CORE_PSYCH_TERMS)
    title_has_computational_signal = _has_any(title_text, COMPUTATIONAL_SIGNAL_TERMS)
    if _has_any(text, NEGATIVE_FRONTIER_TERMS):
        return False

    if item.track == "frontier_core":
        return title_has_core_psych and has_core_psych and has_computational_signal and title_has_computational_signal
    if item.track == "current_program":
        return (
            title_has_core_psych
            and (has_core_psych or has_comorbidity)
            and has_computational_signal
            and title_has_computational_signal
        )
    if item.track == "enabling_signals":
        return title_has_core_psych and has_core_psych and (has_ai or has_institution_signal)
    return False


def _has_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def _normalize_url(url: str) -> str:
    return re.sub(r"[?#].*$", "", url.strip().lower())


def _apply_journal_metadata(item: IntelItem, config: ProjectConfig) -> None:
    if not item.journal_name:
        return
    key = item.journal_name.strip().lower()
    metadata = config.journal_rankings.get(key, {})
    if metadata.get("quartile") and not item.journal_quartile:
        item.journal_quartile = str(metadata["quartile"])
    if metadata.get("impact_factor") and not item.impact_factor:
        item.impact_factor = str(metadata["impact_factor"])
    item.is_top_journal = bool(metadata.get("top_tier", False))


def _venue_bonus(item: IntelItem) -> float:
    bonus = 0.0
    if item.venue_type == "journal":
        bonus += 3.0
        if item.quality_tier == "standard":
            bonus -= 4.0
    if item.venue_type == "preprint":
        bonus -= 6.0
    if item.journal_quartile.upper() == "Q1":
        bonus += 4.0
    if item.journal_quartile.upper() == "Q2":
        bonus += 2.0
    if item.impact_factor:
        try:
            bonus += min(float(item.impact_factor), 20.0) / 5
        except ValueError:
            pass
    return bonus


def _quality_tier(item: IntelItem) -> str:
    quartile = item.journal_quartile.upper()
    if quartile == "Q1":
        return "high"
    if quartile == "Q2":
        return "solid"
    if item.venue_type == "institution":
        return "institution"
    if item.venue_type == "preprint":
        return "preprint"
    if item.venue_type == "journal" and _trusted_domain(item.url):
        return "standard"
    return "unknown"


def _item_evidence_level(item: IntelItem) -> str:
    if item.venue_type == "journal":
        return "peer_reviewed"
    if item.venue_type == "preprint":
        return "preprint"
    if item.venue_type == "institution":
        return "institutional"
    return item.evidence_level


def _condense_summary(text: str, *, limit: int = 148) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return ""
    sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0].strip()
    if len(sentence) <= limit:
        return sentence
    shortened = sentence[: limit - 1].rsplit(" ", 1)[0].strip()
    return f"{shortened}…"


def _passes_quality_gate(item: IntelItem) -> bool:
    if not _valid_http_link(item.url):
        return False
    journal_name = item.journal_name.strip().lower()
    if journal_name and any(term in journal_name for term in LOW_SIGNAL_JOURNAL_TERMS):
        return False
    if item.venue_type in {"institution", "preprint"}:
        return _trusted_domain(item.url)
    if item.venue_type == "journal":
        quartile = item.journal_quartile.upper()
        if quartile in {"Q1", "Q2"}:
            return True
        if item.journal_name and _trusted_domain(item.url):
            return True
        return False
    return _trusted_domain(item.url)


def _passes_curated_gate(item: IntelItem, config: ProjectConfig) -> bool:
    if item.source_admission_status == "reject":
        item.screening_notes.append("Source rejected by admission layer.")
        return False

    if item.curation_tier == "raw_only":
        item.screening_notes.append("Archive source stays in raw intake unless a promotion rule fires.")
    if item.is_archive:
        promoted = _archive_promotion_rule(item, config)
        if not promoted:
            item.screening_notes.append("Archive item kept in raw intake because it lacks promotion evidence.")
        return promoted

    if item.venue_type != "journal":
        if item.venue_type == "institution" and item.corroborating_sources:
            item.screening_notes.append("Institutional item promoted because it is corroborated by another source.")
            return True
        item.screening_notes.append("Non-journal item kept in raw intake.")
        return False

    if item.is_top_journal:
        item.screening_notes.append("Promoted by top-journal whitelist.")
        return True
    if item.quality_tier in {"high", "solid"}:
        item.screening_notes.append("Promoted by journal quality tier.")
        return True
    if item.source_class == "publications_page" and item.corroborating_sources:
        item.screening_notes.append("Promoted because a specialty source is corroborated elsewhere.")
        return True
    item.screening_notes.append("Journal item did not meet curated threshold.")
    return False


def _archive_promotion_rule(item: IntelItem, config: ProjectConfig) -> bool:
    if item.is_top_journal:
        item.screening_notes.append("Archive item linked to a top-journal venue.")
        return True
    if item.study_type in {"review", "trial", "cohort", "policy"}:
        item.screening_notes.append("Archive item promoted by study-type rule.")
        return True
    trusted_corroboration = {
        "pubmed",
        "europe pmc",
        "europepmc",
        "neuroblu",
        "pubmed psychiatry + ai",
        "pubmed psychology + ai",
        "neuroblu publications",
    }
    corroborating = " ".join([item.source, *item.corroborating_sources]).lower()
    if any(token in corroborating for token in trusted_corroboration):
        item.screening_notes.append("Archive item promoted by corroboration from a higher-trust source.")
        return True
    if item.journal_name and config.journal_rankings.get(item.journal_name.strip().lower(), {}).get("top_tier"):
        item.screening_notes.append("Archive item promoted by whitelist-matched journal metadata.")
        return True
    return False


def _trusted_domain(url: str) -> bool:
    hostname = urlparse(url).hostname or ""
    hostname = hostname.lower()
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in TRUSTED_DOMAINS)


def _stable_link(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower()
    if not hostname:
        return False
    if "example.org" in hostname or "iana.org" in hostname:
        return False
    return _trusted_domain(url)


def _valid_http_link(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"}:
        return False
    if not hostname:
        return False
    if "example.org" in hostname or "iana.org" in hostname:
        return False
    return True
