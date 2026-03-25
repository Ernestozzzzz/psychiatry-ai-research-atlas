#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from intel_center.pipeline import run_morning_brief
from intel_center.models import LLMOptions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the psychiatry / psychology + AI research atlas.")
    parser.add_argument("--date", help="Run date in YYYY-MM-DD. Defaults to now.")
    parser.add_argument("--dry-run", action="store_true", help="Render the atlas without writing files.")
    parser.add_argument("--fixture-bundle", type=Path, help="Path to local fixture bundle JSON.")
    parser.add_argument("--open", dest="open_note", action="store_true", help="Open the generated inbox note.")
    parser.add_argument("--skip-open", dest="open_note", action="store_false", help="Do not open the inbox note.")
    parser.add_argument(
        "--github-pages",
        action="store_true",
        help="Export the generated site into docs/ for GitHub Pages publishing.",
    )
    parser.add_argument("--llm", action="store_true", help="Use OpenAI to enhance executive summary and ideas.")
    parser.add_argument(
        "--llm-model",
        default=os.environ.get("INTEL_CENTER_OPENAI_MODEL", "gpt-4o-mini"),
        help="OpenAI model for manual enhancement. Defaults to gpt-4o-mini.",
    )
    parser.set_defaults(open_note=False)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_at = None
    if args.date:
        run_at = datetime.fromisoformat(args.date).replace(tzinfo=UTC)
    llm_options = None
    if args.llm:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print("OPENAI_API_KEY is required when --llm is enabled.", file=sys.stderr)
            return 2
        llm_options = LLMOptions(
            api_key=api_key,
            model=args.llm_model,
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1/responses"),
        )
    result = run_morning_brief(
        root=ROOT,
        run_at=run_at,
        dry_run=args.dry_run,
        open_note=args.open_note,
        fixture_bundle_path=args.fixture_bundle,
        llm_options=llm_options,
    )
    if args.dry_run:
        return 0
    print(f"Wrote dashboard to {result.artifacts.dashboard_page}")
    print(f"Wrote dashboard data to {result.artifacts.dashboard_data}")
    if args.github_pages:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "export_github_pages.py")],
            check=True,
        )
    if result.llm_enhancement:
        print(f"LLM enhancement applied with {result.llm_enhancement.model}")
    elif result.llm_error:
        print(f"LLM enhancement fell back to local heuristics: {result.llm_error}")
    if result.missing_sources:
        print(f"Missing sources: {', '.join(result.missing_sources)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
