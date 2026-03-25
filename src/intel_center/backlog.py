from __future__ import annotations

import json
import re
from dataclasses import asdict
from difflib import SequenceMatcher
from pathlib import Path

from .models import IdeaCandidate


def load_backlog_state(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def merge_ideas(existing: list[dict], new_ideas: list[IdeaCandidate], *, run_date: str, source_note_link: str) -> list[dict]:
    merged = list(existing)
    for idea in new_ideas:
        match = _find_existing(merged, idea.idea_title)
        if match is None:
            merged.append(
                {
                    "slug": _slugify(idea.idea_title),
                    "idea_title": idea.idea_title,
                    "status": "seed",
                    "first_seen": run_date,
                    "last_seen": run_date,
                    "trigger_count": 1,
                    "seed_evidence": idea.seed_evidence,
                    "possible_dataset_or_signal": idea.possible_dataset_or_signal,
                    "candidate_method": idea.candidate_method,
                    "paper_or_project_shape": idea.paper_or_project_shape,
                    "why_now": idea.why_now,
                    "note_links": [source_note_link],
                }
            )
            continue

        match["last_seen"] = run_date
        match["trigger_count"] = int(match.get("trigger_count", 1)) + 1
        match["why_now"] = idea.why_now
        match["possible_dataset_or_signal"] = idea.possible_dataset_or_signal
        match["candidate_method"] = idea.candidate_method
        match["paper_or_project_shape"] = idea.paper_or_project_shape
        note_links = list(match.get("note_links", []))
        if source_note_link not in note_links:
            note_links.append(source_note_link)
        match["note_links"] = note_links[-6:]
        evidence = list(match.get("seed_evidence", []))
        for seed in idea.seed_evidence:
            if seed not in evidence:
                evidence.append(seed)
        match["seed_evidence"] = evidence[-6:]

    merged.sort(key=lambda entry: (entry.get("last_seen", ""), entry.get("trigger_count", 0)), reverse=True)
    return merged


def save_backlog_state(path: Path, backlog: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(backlog, handle, ensure_ascii=False, indent=2)


def render_backlog_markdown(backlog: list[dict]) -> str:
    lines = [
        "---",
        "title: Idea Backlog",
        "tags:",
        "  - intel/ideas",
        "  - psychiatry-ai",
        "type: idea-backlog",
        "---",
        "",
        "# Idea Backlog",
        "",
        "> [!note]",
        "> This note is rendered from machine state in `state/idea_backlog.json`.",
        "",
    ]
    if not backlog:
        lines.extend(["暂无积累条目。", ""])
        return "\n".join(lines)

    for entry in backlog:
        lines.extend(
            [
                f"## {entry['idea_title']}",
                "",
                f"- Status: `{entry['status']}`",
                f"- First Seen: `{entry['first_seen']}`",
                f"- Last Seen: `{entry['last_seen']}`",
                f"- Trigger Count: `{entry['trigger_count']}`",
                f"- Source Notes: {', '.join(entry.get('note_links', [])) or 'None'}",
                "",
                "### Why Now",
                entry.get("why_now", ""),
                "",
                "### Possible Dataset Or Signal",
                entry.get("possible_dataset_or_signal", ""),
                "",
                "### Candidate Method",
                entry.get("candidate_method", ""),
                "",
                "### Paper Or Project Shape",
                entry.get("paper_or_project_shape", ""),
                "",
                "### Seed Evidence",
            ]
        )
        evidence = entry.get("seed_evidence", [])
        if evidence:
            for seed in evidence:
                lines.append(f"- {seed}")
        else:
            lines.append("- None yet")
        lines.append("")
    return "\n".join(lines)


def _find_existing(backlog: list[dict], title: str) -> dict | None:
    normalized = _slugify(title)
    for entry in backlog:
        if entry.get("slug") == normalized:
            return entry
        if SequenceMatcher(None, entry.get("idea_title", ""), title).ratio() >= 0.76:
            return entry
    return None


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text.lower()).strip("-")
