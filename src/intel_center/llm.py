from __future__ import annotations

import json
import urllib.request
from typing import Any, Callable

from .models import IntelItem, LLMEnhancement, LLMOptions


class LLMEnhancementError(RuntimeError):
    """Raised when the optional LLM enhancement flow fails."""


def generate_llm_enhancement(
    *,
    run_date: str,
    items: list[IntelItem],
    options: LLMOptions,
    requester: Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any]] | None = None,
) -> LLMEnhancement:
    payload = {
        "model": options.model,
        "store": False,
        "temperature": 0.2,
        "max_output_tokens": options.max_output_tokens,
        "instructions": _instructions(),
        "input": _build_input(run_date=run_date, items=items),
        "text": {"format": {"type": "json_object"}},
    }
    requester = requester or _post_json
    response = requester(options.base_url, payload, _headers(options.api_key))
    output_text = _extract_output_text(response)
    if not output_text:
        raise LLMEnhancementError("Responses API returned no text output.")

    try:
        blob = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise LLMEnhancementError("LLM output was not valid JSON.") from exc

    bullets = [str(entry).strip() for entry in blob.get("executive_summary_bullets", []) if str(entry).strip()]
    if not bullets:
        raise LLMEnhancementError("LLM enhancement did not return executive summary bullets.")

    usage = response.get("usage") if isinstance(response.get("usage"), dict) else None
    normalized_usage = None
    if usage:
        normalized_usage = {
            "input_tokens": int(usage.get("input_tokens", 0)),
            "output_tokens": int(usage.get("output_tokens", 0)),
            "total_tokens": int(usage.get("total_tokens", 0)),
        }
    return LLMEnhancement(
        executive_summary_bullets=bullets[:5],
        model=options.model,
        usage=normalized_usage,
    )


def _instructions() -> str:
    return (
        "Formatting re-enabled\n"
        "Return valid JSON only. The word JSON is intentional and required.\n"
        "You are enhancing a psychiatry and psychology + AI research dashboard for a researcher.\n"
        "Be concise, evidence-grounded, and research-first.\n"
        "Use English.\n"
        "Do not invent studies, data, or claims beyond the supplied items.\n"
        "Return exactly this top-level key: executive_summary_bullets.\n"
        "executive_summary_bullets must contain 3 to 5 short bullet strings.\n"
        "Prioritize psychiatry, psychology, digital mental health, and clinical AI research over generic AI news.\n"
        "Do not generate research ideas, action plans, or project proposals."
    )


def _build_input(*, run_date: str, items: list[IntelItem]) -> str:
    lines = [
        f"Run date: {run_date}",
        "Task: Produce JSON for the research radar dashboard summary.",
        "Use the evidence below.",
        "",
        "<items>",
    ]
    for index, item in enumerate(items, start=1):
        lines.extend(
            [
                f"[{index}] title: {item.title_en}",
                f"source: {item.source}",
                f"track: {item.track}",
                f"published: {item.published_at.date().isoformat()}",
                f"topics_zh: {', '.join(item.matched_topics_zh)}",
                f"study_type: {item.study_type}",
                f"modality: {item.modality}",
                f"importance_score: {item.importance_score}",
                f"why_important: {item.why_important}",
                f"summary_en: {item.summary_en}",
                f"url: {item.url}",
                "",
            ]
        )
    lines.extend(
        [
            "</items>",
            "",
            "Reminder: Output JSON only, with no markdown fence and no commentary.",
        ]
    )
    return "\n".join(lines)


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _extract_output_text(response: dict[str, Any]) -> str:
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return str(content.get("text", ""))
    return ""
