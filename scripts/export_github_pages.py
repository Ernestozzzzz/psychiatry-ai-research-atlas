#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE_DIR = ROOT / "intel" / "site"
DOCS_DIR = ROOT / "docs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export the generated research atlas as a GitHub Pages site.")
    parser.add_argument(
        "--source",
        type=Path,
        default=SITE_DIR,
        help="Directory that contains index.html and latest.json. Defaults to intel/site.",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=DOCS_DIR,
        help="Output directory for GitHub Pages. Defaults to docs.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.source.resolve()
    target = args.target.resolve()

    index_file = source / "index.html"
    data_file = source / "latest.json"
    if not index_file.exists() or not data_file.exists():
        raise SystemExit(f"Missing site files in {source}. Run scripts/run_morning_brief.py first.")

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    shutil.copy2(index_file, target / "index.html")
    shutil.copy2(data_file, target / "latest.json")
    (target / ".nojekyll").write_text("", encoding="utf-8")

    print(f"Exported GitHub Pages site to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
