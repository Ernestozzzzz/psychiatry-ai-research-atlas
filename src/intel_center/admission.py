from __future__ import annotations

from .models import CandidateDefinition, CandidateEvaluation

IMPLEMENTATION_RECOMMENDATIONS = {
    "source": "直接接入",
    "workflow_reference": "只借鉴结构，不直接依赖",
    "ranking_helper": "只借鉴结构，不直接依赖",
}


def evaluate_candidates(candidates: list[CandidateDefinition], rubric: dict) -> list[CandidateEvaluation]:
    weights = dict(rubric.get("weights", {}))
    adopt_threshold = float(rubric.get("adopt_threshold", 80))
    review_threshold = float(rubric.get("review_threshold", 60))
    blocking_floor = float(rubric.get("blocking_floor", 2))

    evaluations: list[CandidateEvaluation] = []
    for candidate in candidates:
        weighted_total = 0.0
        for dimension, weight in weights.items():
            weighted_total += float(candidate.scores.get(dimension, 0.0)) * weight
        admission_score = round(weighted_total * 20, 2)

        if admission_score >= adopt_threshold:
            admission_status = "adopt"
        elif admission_score >= review_threshold:
            admission_status = "review"
        else:
            admission_status = "reject"

        if (
            float(candidate.scores.get("provenance", 0.0)) < blocking_floor
            or float(candidate.scores.get("safety", 0.0)) < blocking_floor
        ) and admission_status == "adopt":
            admission_status = "review"

        implementation_recommendation = _implementation_recommendation(candidate, admission_status)
        evaluations.append(
            CandidateEvaluation(
                candidate_id=candidate.candidate_id,
                dimension_scores={key: float(value) for key, value in candidate.scores.items()},
                weighted_total=round(weighted_total, 3),
                admission_score=admission_score,
                admission_status=admission_status,
                implementation_recommendation=implementation_recommendation,
                key_risks=list(candidate.key_risks),
                borrow_notes=list(candidate.borrow_notes),
                suitable_for=list(candidate.suitable_for),
                review_recommendation=candidate.review_recommendation,
            )
        )
    evaluations.sort(key=lambda entry: (entry.admission_score, entry.candidate_id), reverse=True)
    return evaluations


def _implementation_recommendation(candidate: CandidateDefinition, admission_status: str) -> str:
    if admission_status == "reject":
        return "暂不采用"
    if "source" in candidate.suitable_for or ("source_strategy" in candidate.suitable_for and admission_status == "adopt"):
        return "直接接入"
    for role in ("workflow_reference", "ranking_helper"):
        if role in candidate.suitable_for:
            return IMPLEMENTATION_RECOMMENDATIONS[role]
    return "只借鉴结构，不直接依赖" if admission_status == "review" else "直接接入"
