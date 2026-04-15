#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from intel_center.delivery import build_weixin_brief_message
from intel_center.delivery import build_weixin_brief_message_from_payload, is_payload_fresh_for_date, load_latest_brief_payload
from intel_center.delivery import load_delivery_log, record_delivery, was_message_delivered
from intel_center.models import LLMOptions
from intel_center.pipeline import run_morning_brief
from intel_center.weixin_bridge import build_bridge_config, default_state_path, resolve_push_user_ids, send_push_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the daily intelligence brief and push it to WeChat.")
    parser.add_argument("--date", help="Run date in YYYY-MM-DD. Defaults to now.")
    parser.add_argument("--fixture-bundle", type=Path, help="Path to local fixture bundle JSON.")
    parser.add_argument("--state-path", type=Path, default=default_state_path(), help="Path to WeChat bridge state JSON.")
    parser.add_argument(
        "--latest-run-path",
        type=Path,
        default=ROOT / "state" / "latest_run.json",
        help="Path to the cached latest brief payload used for fast repeat pushes.",
    )
    parser.add_argument(
        "--delivery-log-path",
        type=Path,
        default=ROOT / "state" / "weixin_delivery.json",
        help="Path to the delivery log used to suppress duplicate pushes.",
    )
    parser.add_argument("--to-user", action="append", default=[], help="Optional target WeChat user id. Defaults to all known peers.")
    parser.add_argument("--max-curated", type=int, default=5, help="Maximum curated items to include in the push message.")
    parser.add_argument("--max-raw", type=int, default=3, help="Maximum raw-intake items to include in the push message.")
    parser.add_argument("--print-only", action="store_true", help="Print the outgoing message instead of sending it.")
    parser.add_argument("--refresh", action="store_true", help="Force a fresh research run instead of reusing today's cached brief.")
    parser.add_argument("--force", action="store_true", help="Force sending even if the same brief was already delivered today.")
    parser.add_argument("--llm", action="store_true", help="Use OpenAI to enhance executive summary bullets before pushing.")
    parser.add_argument(
        "--llm-model",
        default=os.environ.get("INTEL_CENTER_OPENAI_MODEL", "gpt-4o-mini"),
        help="OpenAI model for optional enhancement.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_at = None
    target_date = datetime.now(UTC).date().isoformat()
    if args.date:
        run_at = datetime.fromisoformat(args.date).replace(tzinfo=UTC)
        target_date = run_at.date().isoformat()
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

    payload = None
    message = ""
    if not args.refresh and args.latest_run_path.exists():
        cached_payload = load_latest_brief_payload(args.latest_run_path)
        if is_payload_fresh_for_date(cached_payload, target_date=target_date):
            payload = cached_payload
            payload["dashboard_page"] = str(ROOT / "intel" / "site" / "index.html")
            message = build_weixin_brief_message_from_payload(
                payload,
                max_curated_items=args.max_curated,
                max_raw_items=args.max_raw,
            )

    if not message:
        result = run_morning_brief(
            root=ROOT,
            run_at=run_at,
            dry_run=False,
            open_note=False,
            fixture_bundle_path=args.fixture_bundle,
            llm_options=llm_options,
        )
        message = build_weixin_brief_message(
            result,
            max_curated_items=args.max_curated,
            max_raw_items=args.max_raw,
        )

    print(message)
    if args.print_only:
        return 0

    config = build_bridge_config(workspace=ROOT, state_path=args.state_path)
    target_user_ids = resolve_push_user_ids(config, peer_user_ids=args.to_user or None)
    delivery_log = load_delivery_log(args.delivery_log_path)
    if not args.force and was_message_delivered(
        delivery_log,
        run_date=target_date,
        message=message,
        peer_user_ids=target_user_ids,
    ):
        print("")
        print(f"Skipped duplicate delivery for {len(target_user_ids)} peer(s). Use --force to resend.")
        return 0

    delivered = send_push_text(config, message, peer_user_ids=target_user_ids)
    record_delivery(
        args.delivery_log_path,
        run_date=target_date,
        message=message,
        peer_user_ids=delivered,
    )
    print("")
    print(f"Delivered to {len(delivered)} peer(s): {', '.join(delivered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
