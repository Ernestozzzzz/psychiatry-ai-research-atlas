from __future__ import annotations

from collections import Counter
import json
from datetime import datetime
from html import escape

from .models import IntelItem

TRACK_LABELS = {
    "frontier_core": {"en": "Frontier", "zh": "前沿"},
    "current_program": {"en": "Clinical", "zh": "临床"},
    "enabling_signals": {"en": "Signals", "zh": "信号"},
}


def build_executive_bullets(items: list[IntelItem], missing_sources: list[str], *, days_back: int) -> dict[str, list[str]]:
    window_en = "the last 12 months" if days_back >= 365 else f"the last {days_back} days"
    window_zh = "最近 12 个月" if days_back >= 365 else f"最近 {days_back} 天"
    if items:
        top_source = items[0].source
        coverage_en = f"This feed highlights psychiatry and psychology + AI work from {window_en}, with the strongest recent signal coming from {top_source}."
        coverage_zh = f"这个 feed 聚焦 {window_zh} 的 psychiatry / psychology + AI 研究，当前最强来源是 {top_source}。"
    else:
        coverage_en = f"This feed scans {window_en}, but no item was strong enough for the front page in this run."
        coverage_zh = f"这个 feed 正在扫描 {window_zh} 的研究，但本轮没有足够强的条目进入首页。"

    frontier_count = sum(1 for item in items if item.track == "frontier_core")
    clinical_count = sum(1 for item in items if item.track == "current_program")
    signal_count = sum(1 for item in items if item.track == "enabling_signals")
    peer_reviewed_count = sum(1 for item in items if item.venue_type == "journal")
    high_trust_count = sum(1 for item in items if item.quality_tier in {"high", "solid"})
    preprint_count = sum(1 for item in items if item.venue_type == "preprint")
    top_modalities = [
        name
        for name, _ in Counter(item.modality for item in items if item.modality and item.modality != "general").most_common(2)
    ]
    modality_en = ", ".join(top_modalities) if top_modalities else "mixed modalities"
    modality_zh = "、".join(top_modalities) if top_modalities else "多种模态"

    bullets_en = [
        coverage_en,
        f"The archive currently holds {high_trust_count} Q1/Q2 items, {peer_reviewed_count} peer-reviewed papers, and {preprint_count} preprints.",
        f"Most items cluster around {modality_en}, with {frontier_count} frontier items, {clinical_count} clinical items, and {signal_count} enabling signals.",
        "The default view is high-signal only: the left accent marks track, while badge colors mark evidence level and venue quality.",
    ]
    bullets_zh = [
        coverage_zh,
        f"当前档案中共有 {high_trust_count} 条 Q1/Q2 条目、{peer_reviewed_count} 条同行评议论文，以及 {preprint_count} 条预印本。",
        f"研究主要集中在 {modality_zh}，其中前沿研究 {frontier_count} 条，临床研究 {clinical_count} 条，配套信号 {signal_count} 条。",
        "首页默认显示 high-signal 视图：左侧色条表示轨道，badge 颜色表示证据层级与期刊质量。",
    ]
    if missing_sources:
        bullets_en.append(
            f"{len(missing_sources)} source(s) were unavailable in this run, so the page stays selective instead of filling gaps with weaker content."
        )
        bullets_zh.append(f"本轮有 {len(missing_sources)} 个来源不可用，所以页面会保持克制，不会用弱相关内容补位。")
    return {"en": bullets_en[:4], "zh": bullets_zh[:4]}


def build_dashboard_payload(
    *,
    run_date: str,
    generated_at: datetime,
    days_back: int,
    items: list[IntelItem],
    missing_sources: list[str],
    executive_summary: dict[str, list[str]],
    initial_render_count: int,
    llm_note: str | None = None,
) -> dict:
    track_counts = {track: sum(1 for item in items if item.track == track) for track in TRACK_LABELS}
    quality_counts = {
        "high_signal": sum(1 for item in items if item.quality_tier in {"high", "solid"}),
        "peer_reviewed": sum(1 for item in items if item.venue_type == "journal"),
        "preprint": sum(1 for item in items if item.venue_type == "preprint"),
    }
    if items:
        coverage = {
            "start": min(item.published_at for item in items).date().isoformat(),
            "end": max(item.published_at for item in items).date().isoformat(),
        }
    else:
        coverage = {"start": run_date, "end": run_date}
    source_total = len(items) + len(missing_sources)
    months = _build_month_options(items)
    return {
        "title": "Psychiatry + Psychology AI Research Atlas",
        "subtitle": {
            "en": "A shareable archive for psychiatry, psychology, digital mental health, and clinical AI research.",
            "zh": "一个可分享的 psychiatry、psychology、digital mental health 与 clinical AI 研究档案。",
        },
        "run_date": run_date,
        "generated_at": generated_at.isoformat(),
        "days_back": days_back,
        "coverage": coverage,
        "executive_summary": executive_summary,
        "llm_note": llm_note,
        "missing_sources": missing_sources,
        "initial_render_count": initial_render_count,
        "stats": {
            "item_count": len(items),
            "frontier_count": track_counts["frontier_core"],
            "clinical_count": track_counts["current_program"],
            "signal_count": track_counts["enabling_signals"],
            "high_signal_count": quality_counts["high_signal"],
            "peer_reviewed_count": quality_counts["peer_reviewed"],
            "preprint_count": quality_counts["preprint"],
            "source_ok_count": source_total - len(missing_sources),
            "source_total_count": source_total,
            "month_count": len(months),
        },
        "tracks": [
            {
                "key": track,
                "label_en": TRACK_LABELS[track]["en"],
                "label_zh": TRACK_LABELS[track]["zh"],
                "count": track_counts[track],
            }
            for track in TRACK_LABELS
        ],
        "months": months,
        "items": [_serialize_item(item) for item in items],
    }


def render_dashboard_html(payload: dict) -> str:
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    title = escape(payload["title"])
    subtitle = escape(payload["subtitle"]["en"])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{
      --bg: #f4efe4;
      --paper: rgba(255, 250, 242, 0.9);
      --ink: #13211b;
      --muted: #5f695f;
      --line: rgba(24, 50, 39, 0.12);
      --accent: #1f5a45;
      --accent-warm: #a86a3d;
      --accent-cool: #2f6c74;
      --shadow: 0 18px 60px rgba(23, 39, 31, 0.12);
      --sans: "Avenir Next", "Segoe UI", "Helvetica Neue", sans-serif;
      --serif: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: var(--sans);
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(168, 106, 61, 0.16), transparent 28%),
        radial-gradient(circle at top right, rgba(47, 108, 116, 0.16), transparent 24%),
        linear-gradient(180deg, #ede5d6 0%, var(--bg) 55%, #efe7d8 100%);
      min-height: 100vh;
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      background-image:
        linear-gradient(rgba(19, 33, 27, 0.04) 1px, transparent 1px),
        linear-gradient(90deg, rgba(19, 33, 27, 0.04) 1px, transparent 1px);
      background-size: 36px 36px;
      pointer-events: none;
      mask-image: linear-gradient(180deg, rgba(0,0,0,0.3), transparent 80%);
    }}
    .page {{ width: min(1280px, calc(100vw - 28px)); margin: 18px auto 48px; position: relative; }}
    .hero {{
      padding: 28px;
      border-radius: 28px;
      background: linear-gradient(145deg, rgba(20, 50, 37, 0.96), rgba(22, 68, 64, 0.93));
      color: #f7f0e4;
      box-shadow: var(--shadow);
      position: relative;
      overflow: hidden;
    }}
    .hero::after {{
      content: "";
      position: absolute;
      inset: auto -10% -35% 45%;
      height: 280px;
      background: radial-gradient(circle, rgba(255, 223, 181, 0.36), transparent 65%);
      pointer-events: none;
    }}
    .hero-top {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
      flex-wrap: wrap;
    }}
    .eyebrow {{
      letter-spacing: 0.18em;
      text-transform: uppercase;
      font-size: 11px;
      color: rgba(247, 240, 228, 0.7);
      margin-bottom: 14px;
    }}
    h1 {{ margin: 0; font-family: var(--serif); font-size: clamp(2.2rem, 5vw, 4.4rem); line-height: 0.94; max-width: 10em; }}
    .subtitle {{ margin: 18px 0 0; max-width: 52rem; color: rgba(247, 240, 228, 0.84); line-height: 1.6; font-size: 1rem; }}
    .hero-meta {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 22px; }}
    .hero-chip {{
      border: 1px solid rgba(247, 240, 228, 0.15);
      padding: 9px 12px;
      border-radius: 999px;
      color: rgba(247, 240, 228, 0.9);
      backdrop-filter: blur(6px);
      background: rgba(255, 255, 255, 0.06);
      font-size: 0.93rem;
    }}
    .toggle-group {{
      display: inline-flex;
      gap: 8px;
      padding: 5px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.1);
      z-index: 1;
    }}
    .toggle-button {{
      border: 0;
      background: transparent;
      color: rgba(247, 240, 228, 0.82);
      padding: 8px 12px;
      border-radius: 999px;
      font: inherit;
      cursor: pointer;
    }}
    .toggle-button.is-active {{
      background: rgba(255, 248, 235, 0.18);
      color: #fff4e6;
    }}
    .layout {{ display: grid; grid-template-columns: 340px minmax(0, 1fr); gap: 18px; margin-top: 18px; }}
    .panel {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 24px;
      padding: 22px;
      box-shadow: 0 12px 40px rgba(28, 43, 35, 0.08);
      backdrop-filter: blur(8px);
    }}
    .panel h2, .panel h3 {{ margin: 0 0 12px; font-family: var(--serif); font-weight: 700; letter-spacing: -0.02em; }}
    .summary-list {{ margin: 0; padding-left: 18px; line-height: 1.7; }}
    .summary-list li + li {{ margin-top: 8px; }}
    .stats {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 16px; }}
    .stat-card {{
      padding: 14px;
      border-radius: 18px;
      background: linear-gradient(180deg, rgba(255,255,255,0.82), rgba(220,233,224,0.72));
      border: 1px solid rgba(31, 90, 69, 0.12);
    }}
    .stat-label {{ font-size: 0.82rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.1em; }}
    .stat-value {{ font-size: 1.5rem; margin-top: 8px; font-weight: 700; }}
    .status-panel {{
      margin-top: 16px;
      padding: 16px;
      border-radius: 18px;
      background: rgba(142, 67, 56, 0.08);
      border: 1px solid rgba(142, 67, 56, 0.16);
    }}
    .status-panel p {{ margin: 0 0 10px; color: #5b312a; line-height: 1.6; }}
    .missing-list {{ margin: 0; padding-left: 18px; color: #5b312a; }}
    .legend {{ margin-top: 16px; display: grid; gap: 10px; }}
    .legend-row {{ display: flex; align-items: center; gap: 10px; color: var(--muted); font-size: 0.92rem; }}
    .legend-swatch {{ width: 14px; height: 14px; border-radius: 999px; flex: 0 0 auto; }}
    .legend-swatch.frontier {{ background: var(--accent); }}
    .legend-swatch.clinical {{ background: var(--accent-warm); }}
    .legend-swatch.signals {{ background: var(--accent-cool); }}
    .legend-swatch.high {{ background: #b07a22; }}
    .legend-swatch.solid {{ background: #8c6130; }}
    .legend-swatch.preprint {{ background: #855b82; }}
    .legend-swatch.institution {{ background: #2f6c74; }}
    .toolbar {{
      display: grid;
      gap: 14px;
      margin-bottom: 18px;
    }}
    .filter-row {{
      display: flex;
      gap: 10px;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
    }}
    .filter-group {{
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }}
    .filter-label {{
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-size: 0.74rem;
    }}
    .track-pills, .evidence-pills {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .filter-pill {{
      background: transparent;
      border: 1px solid rgba(19, 33, 27, 0.15);
      color: var(--ink);
      border-radius: 999px;
      padding: 10px 14px;
      cursor: pointer;
      font: inherit;
      transition: 180ms ease;
    }}
    .filter-pill span {{ color: var(--muted); margin-left: 6px; }}
    .filter-pill.is-active, .filter-pill:hover {{
      background: var(--accent);
      color: #f7f0e4;
      border-color: var(--accent);
    }}
    .filter-pill.is-active span, .filter-pill:hover span {{ color: rgba(247, 240, 228, 0.82); }}
    .month-select, .sort-select {{
      border: 1px solid rgba(19, 33, 27, 0.15);
      background: rgba(255,255,255,0.82);
      border-radius: 999px;
      padding: 11px 16px;
      font: inherit;
      color: var(--ink);
    }}
    .stream-header {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 12px;
      flex-wrap: wrap;
      margin-bottom: 14px;
    }}
    .stream-title {{ margin: 0; font-size: 1.7rem; }}
    .stream-note {{ color: var(--muted); font-size: 0.94rem; }}
    .card-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    .intel-card {{
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 18px;
      background: linear-gradient(180deg, rgba(255,255,255,0.92), rgba(247,242,234,0.94));
      position: relative;
      overflow: hidden;
      min-height: 260px;
    }}
    .intel-card::before {{ content: ""; position: absolute; inset: 0 auto 0 0; width: 5px; background: var(--accent); }}
    .intel-card[data-track="current_program"]::before {{ background: var(--accent-warm); }}
    .intel-card[data-track="enabling_signals"]::before {{ background: var(--accent-cool); }}
    .intel-card[data-quality="high"] {{ box-shadow: inset 0 0 0 1px rgba(176, 122, 34, 0.28); }}
    .intel-card[data-quality="solid"] {{ box-shadow: inset 0 0 0 1px rgba(140, 97, 48, 0.22); }}
    .intel-card[data-quality="preprint"] {{ box-shadow: inset 0 0 0 1px rgba(133, 91, 130, 0.22); }}
    .intel-card[data-quality="institution"] {{ box-shadow: inset 0 0 0 1px rgba(47, 108, 116, 0.2); }}
    .card-meta {{ display: flex; flex-wrap: wrap; gap: 8px; color: var(--muted); font-size: 0.86rem; margin-bottom: 10px; }}
    .venue-row {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
      margin-bottom: 12px;
    }}
    .venue-name {{
      font-weight: 700;
      color: var(--ink);
      background: rgba(19, 33, 27, 0.05);
      border-radius: 999px;
      padding: 7px 12px;
    }}
    .metric-badge, .tag {{
      display: inline-flex;
      align-items: center;
      padding: 5px 10px;
      border-radius: 999px;
      font-size: 0.8rem;
    }}
    .metric-badge {{ background: rgba(168, 106, 61, 0.12); color: #74441e; }}
    .metric-badge[data-quality="high"] {{ background: rgba(176, 122, 34, 0.16); color: #7a5416; }}
    .metric-badge[data-quality="solid"] {{ background: rgba(140, 97, 48, 0.14); color: #69451f; }}
    .metric-badge[data-quality="preprint"] {{ background: rgba(133, 91, 130, 0.14); color: #684566; }}
    .metric-badge[data-quality="institution"] {{ background: rgba(47, 108, 116, 0.14); color: #214d52; }}
    .metric-badge[data-quality="unknown"] {{ background: rgba(95, 105, 95, 0.14); color: #495149; }}
    .tag {{ background: rgba(31, 90, 69, 0.08); color: var(--accent); }}
    .intel-card h3 {{ margin: 0 0 10px; font-size: 1.08rem; line-height: 1.45; }}
    .signal-line {{
      margin: 0 0 12px;
      color: #4b564d;
      font-size: 0.9rem;
      line-height: 1.6;
    }}
    .summary {{ color: #26352d; line-height: 1.7; margin: 0 0 14px; }}
    .card-footer {{
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: auto;
    }}
    .source-link {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--accent);
      text-decoration: none;
      font-weight: 700;
    }}
    .empty-state {{
      border: 1px dashed rgba(31, 90, 69, 0.28);
      border-radius: 20px;
      padding: 28px;
      color: var(--muted);
      background: rgba(255,255,255,0.45);
    }}
    .load-more-wrap {{ display: flex; justify-content: center; margin-top: 18px; }}
    .load-more {{
      border: 0;
      border-radius: 999px;
      padding: 12px 18px;
      background: var(--accent);
      color: #f7f0e4;
      font: inherit;
      cursor: pointer;
      box-shadow: 0 12px 30px rgba(31, 90, 69, 0.18);
    }}
    .load-more[hidden], #feed-sentinel[hidden] {{ display: none; }}
    #feed-sentinel {{ height: 1px; }}
    @media (max-width: 980px) {{
      .layout, .card-grid {{ grid-template-columns: 1fr; }}
      .month-select, .sort-select {{ width: 100%; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <header class="hero">
      <div class="hero-top">
        <div>
          <div class="eyebrow">Psychiatry + Psychology AI Research Atlas</div>
          <h1 id="hero-title">Research Atlas</h1>
        </div>
        <div class="toggle-group" aria-label="Language switcher">
          <button class="toggle-button is-active" data-lang="en">English</button>
          <button class="toggle-button" data-lang="zh">中文</button>
        </div>
      </div>
      <p class="subtitle" id="hero-subtitle">{subtitle}</p>
      <div class="hero-meta">
        <div class="hero-chip" id="chip-run-date">Run date: {escape(payload["run_date"])}</div>
        <div class="hero-chip" id="chip-window">Coverage window: last 12 months</div>
        <div class="hero-chip" id="chip-range">Published range: {escape(payload["coverage"]["start"])} to {escape(payload["coverage"]["end"])}</div>
        <div class="hero-chip" id="chip-items">Items in archive: {payload["stats"]["item_count"]}</div>
      </div>
      {"<div class=\"hero-chip\" style=\"margin-top:16px; display:inline-flex;\">" + escape(payload["llm_note"]) + "</div>" if payload.get("llm_note") else ""}
    </header>

    <div class="layout">
      <aside class="panel">
        <h2 id="summary-title">Overview</h2>
        <ul class="summary-list" id="summary-list"></ul>
        <div class="stats">
          <div class="stat-card"><div class="stat-label" id="stat-high-signal-label">High-Signal</div><div class="stat-value">{payload["stats"]["high_signal_count"]}</div></div>
          <div class="stat-card"><div class="stat-label" id="stat-peer-reviewed-label">Peer-Reviewed</div><div class="stat-value">{payload["stats"]["peer_reviewed_count"]}</div></div>
          <div class="stat-card"><div class="stat-label" id="stat-preprints-label">Preprints</div><div class="stat-value">{payload["stats"]["preprint_count"]}</div></div>
          <div class="stat-card"><div class="stat-label" id="stat-months-label">Months</div><div class="stat-value">{payload["stats"]["month_count"]}</div></div>
        </div>
        <section class="status-panel" id="missing-panel" hidden>
          <h3 id="missing-title">Unavailable Sources</h3>
          <p id="missing-copy"></p>
          <ul class="missing-list" id="missing-list"></ul>
        </section>
        <div class="legend" id="color-legend">
          <div class="legend-row"><span class="legend-swatch frontier"></span><span id="legend-frontier">Green border: frontier research</span></div>
          <div class="legend-row"><span class="legend-swatch clinical"></span><span id="legend-clinical">Copper border: clinical or program-facing work</span></div>
          <div class="legend-row"><span class="legend-swatch signals"></span><span id="legend-signals">Teal border: enabling institutions and infrastructure</span></div>
          <div class="legend-row"><span class="legend-swatch high"></span><span id="legend-high">Gold badge: Q1 / highest-trust venue</span></div>
          <div class="legend-row"><span class="legend-swatch preprint"></span><span id="legend-preprint">Plum badge: preprint</span></div>
        </div>
      </aside>

      <main class="panel">
        <div class="toolbar">
          <div class="filter-row">
            <div class="filter-group">
              <span class="filter-label" id="track-filter-label">Track</span>
              <div class="track-pills" id="filter-toolbar"></div>
            </div>
          </div>
          <div class="filter-row">
            <div class="filter-group">
              <span class="filter-label" id="evidence-filter-label">Evidence</span>
              <div class="evidence-pills" id="evidence-toolbar"></div>
            </div>
            <div class="filter-group">
              <span class="filter-label" id="month-filter-label">Month</span>
              <select class="month-select" id="month-select"></select>
              <span class="filter-label" id="sort-filter-label">Sort</span>
              <select class="sort-select" id="sort-select"></select>
            </div>
          </div>
        </div>
        <div class="stream-header">
          <h2 class="stream-title" id="stream-title">Archive Feed</h2>
          <div class="stream-note" id="stream-note">Default view shows high-signal papers first. Choose a month or keep scrolling to load more cards.</div>
        </div>
        <div class="card-grid" id="card-grid"></div>
        <div class="empty-state" id="empty-state" hidden></div>
        <div class="load-more-wrap">
          <button class="load-more" id="load-more" hidden>Load more</button>
        </div>
        <div id="feed-sentinel"></div>
      </main>
    </div>
  </div>

  <script>
    const payload = {data_json};
    const translations = {{
      en: {{
        heroTitle: 'Research Atlas',
        subtitle: payload.subtitle.en,
        summaryTitle: 'Overview',
        statHighSignal: 'High-Signal',
        statPeerReviewed: 'Peer-Reviewed',
        statPreprints: 'Preprints',
        statMonths: 'Months',
        trackFilter: 'Track',
        evidenceFilter: 'Evidence',
        monthFilter: 'Month',
        sortFilter: 'Sort',
        trackAll: 'All',
        evidenceAll: 'All',
        evidenceHighSignal: 'High-signal',
        evidencePeerReviewed: 'Peer-reviewed',
        evidencePreprints: 'Preprints',
        monthAll: 'All months',
        sortBest: 'Best first',
        sortNewest: 'Newest first',
        streamTitle: 'Archive Feed',
        streamNote: 'Default view shows high-signal papers first. Choose a month or keep scrolling to load more cards.',
        runDate: `Run date: ${{payload.run_date}}`,
        coverageWindow: 'Coverage window: last 12 months',
        publishedRange: `Published range: ${{payload.coverage.start}} to ${{payload.coverage.end}}`,
        itemCount: `Items in archive: ${{payload.stats.item_count}}`,
        sourceUnavailableTitle: 'Unavailable Sources',
        sourceUnavailableCopy: 'These sources failed in the current run. The page stays selective instead of filling gaps with weaker content.',
        legendFrontier: 'Green border: frontier research',
        legendClinical: 'Copper border: clinical or program-facing work',
        legendSignals: 'Teal border: enabling institutions and infrastructure',
        legendHigh: 'Gold badge: Q1 / highest-trust venue',
        legendPreprint: 'Plum badge: preprint',
        openSource: 'Open source ↗',
        loadMore: 'Load more',
        noItems: 'No strong items match the current track / month filter.',
        signalLinePrefix: 'Signal',
        venueUnknown: 'Venue unavailable',
        preprint: 'Preprint',
        institution: 'Institution',
      }},
      zh: {{
        heroTitle: '研究档案',
        subtitle: payload.subtitle.zh,
        summaryTitle: '概览',
        statHighSignal: '高信号',
        statPeerReviewed: '同行评议',
        statPreprints: '预印本',
        statMonths: '月份',
        trackFilter: '轨道',
        evidenceFilter: '证据',
        monthFilter: '月份',
        sortFilter: '排序',
        trackAll: '全部',
        evidenceAll: '全部',
        evidenceHighSignal: '高信号',
        evidencePeerReviewed: '同行评议',
        evidencePreprints: '预印本',
        monthAll: '全部月份',
        sortBest: '优先高质量',
        sortNewest: '最新优先',
        streamTitle: '档案信息流',
        streamNote: '首页默认优先显示高信号论文。你可以选择月份，也可以继续滚动加载更多卡片。',
        runDate: `生成日期：${{payload.run_date}}`,
        coverageWindow: '覆盖窗口：最近 12 个月',
        publishedRange: `发布时间范围：${{payload.coverage.start}} 至 ${{payload.coverage.end}}`,
        itemCount: `档案条目：${{payload.stats.item_count}}`,
        sourceUnavailableTitle: '不可用来源',
        sourceUnavailableCopy: '这些来源本轮抓取失败，所以页面会保持克制，不会用弱相关内容补位。',
        legendFrontier: '绿色边框：前沿研究',
        legendClinical: '铜色边框：临床或课题相关研究',
        legendSignals: '青色边框：机构与基础设施信号',
        legendHigh: '金色 badge：Q1 / 高信任度期刊',
        legendPreprint: '紫色 badge：预印本',
        openSource: '打开原文 ↗',
        loadMore: '加载更多',
        noItems: '当前轨道或月份筛选下没有足够强的条目。',
        signalLinePrefix: '信号',
        venueUnknown: '来源未标注',
        preprint: '预印本',
        institution: '机构来源',
      }},
    }};

    let lang = 'en';
    let activeTrack = 'all';
    let activeEvidence = 'high_signal';
    let activeMonth = 'all';
    let activeSort = 'best';
    let visibleCount = payload.initial_render_count;

    const summaryList = document.getElementById('summary-list');
    const filterToolbar = document.getElementById('filter-toolbar');
    const evidenceToolbar = document.getElementById('evidence-toolbar');
    const monthSelect = document.getElementById('month-select');
    const sortSelect = document.getElementById('sort-select');
    const cardGrid = document.getElementById('card-grid');
    const loadMoreButton = document.getElementById('load-more');
    const emptyState = document.getElementById('empty-state');
    const missingPanel = document.getElementById('missing-panel');
    const missingList = document.getElementById('missing-list');
    const sentinel = document.getElementById('feed-sentinel');

    function escapeHtml(value) {{
      return String(value).replace(/[&<>"']/g, (char) => {{
        const map = {{ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }};
        return map[char];
      }});
    }}

    function t(key) {{
      return translations[lang][key];
    }}

    function monthLabel(month) {{
      if (lang === 'zh') {{
        return month.label_zh;
      }}
      return month.label_en;
    }}

    function trackLabel(track) {{
      const trackMeta = payload.tracks.find((entry) => entry.key === track);
      if (!trackMeta) {{
        return track;
      }}
      return lang === 'en' ? trackMeta.label_en : trackMeta.label_zh;
    }}

    function evidenceCount(filterKey) {{
      if (filterKey === 'all') {{
        return payload.stats.item_count;
      }}
      if (filterKey === 'high_signal') {{
        return payload.stats.high_signal_count;
      }}
      if (filterKey === 'peer_reviewed') {{
        return payload.stats.peer_reviewed_count;
      }}
      if (filterKey === 'preprint') {{
        return payload.stats.preprint_count;
      }}
      return 0;
    }}

    function evidenceMatch(item) {{
      if (activeEvidence === 'all') {{
        return true;
      }}
      if (activeEvidence === 'high_signal') {{
        return ['high', 'solid'].includes(item.quality_tier);
      }}
      if (activeEvidence === 'peer_reviewed') {{
        return item.venue_type === 'journal';
      }}
      if (activeEvidence === 'preprint') {{
        return item.venue_type === 'preprint';
      }}
      return true;
    }}

    function compareItems(a, b) {{
      if (activeSort === 'newest') {{
        if (a.published_at === b.published_at) {{
          return b.importance_score - a.importance_score;
        }}
        return a.published_at < b.published_at ? 1 : -1;
      }}
      if (a.importance_score === b.importance_score) {{
        return a.published_at < b.published_at ? 1 : -1;
      }}
      return b.importance_score - a.importance_score;
    }}

    function filteredItems() {{
      const items = payload.items.slice().sort(compareItems);
      return items.filter((item) => {{
        const trackOk = activeTrack === 'all' || item.track === activeTrack;
        const monthOk = activeMonth === 'all' || item.month_key === activeMonth;
        return trackOk && monthOk && evidenceMatch(item);
      }});
    }}

    function renderSummary() {{
      summaryList.innerHTML = payload.executive_summary[lang].map((line) => `<li>${{escapeHtml(line)}}</li>`).join('');
      document.getElementById('hero-title').textContent = t('heroTitle');
      document.getElementById('hero-subtitle').textContent = t('subtitle');
      document.getElementById('summary-title').textContent = t('summaryTitle');
      document.getElementById('stat-high-signal-label').textContent = t('statHighSignal');
      document.getElementById('stat-peer-reviewed-label').textContent = t('statPeerReviewed');
      document.getElementById('stat-preprints-label').textContent = t('statPreprints');
      document.getElementById('stat-months-label').textContent = t('statMonths');
      document.getElementById('track-filter-label').textContent = t('trackFilter');
      document.getElementById('evidence-filter-label').textContent = t('evidenceFilter');
      document.getElementById('month-filter-label').textContent = t('monthFilter');
      document.getElementById('sort-filter-label').textContent = t('sortFilter');
      document.getElementById('stream-title').textContent = t('streamTitle');
      document.getElementById('stream-note').textContent = t('streamNote');
      document.getElementById('chip-run-date').textContent = t('runDate');
      document.getElementById('chip-window').textContent = t('coverageWindow');
      document.getElementById('chip-range').textContent = t('publishedRange');
      document.getElementById('chip-items').textContent = t('itemCount');
      document.getElementById('legend-frontier').textContent = t('legendFrontier');
      document.getElementById('legend-clinical').textContent = t('legendClinical');
      document.getElementById('legend-signals').textContent = t('legendSignals');
      document.getElementById('legend-high').textContent = t('legendHigh');
      document.getElementById('legend-preprint').textContent = t('legendPreprint');
      document.documentElement.lang = lang === 'en' ? 'en' : 'zh';
    }}

    function renderFilters() {{
      const buttons = [
        `<button class="filter-pill ${{activeTrack === 'all' ? 'is-active' : ''}}" data-track="all">${{escapeHtml(t('trackAll'))}} <span>${{payload.stats.item_count}}</span></button>`,
        ...payload.tracks.map((track) => {{
          const label = lang === 'en' ? track.label_en : track.label_zh;
          return `<button class="filter-pill ${{activeTrack === track.key ? 'is-active' : ''}}" data-track="${{escapeHtml(track.key)}}">${{escapeHtml(label)}} <span>${{track.count}}</span></button>`;
        }}),
      ];
      filterToolbar.innerHTML = buttons.join('');
      filterToolbar.querySelectorAll('.filter-pill').forEach((button) => {{
        button.addEventListener('click', () => {{
          activeTrack = button.dataset.track;
          visibleCount = payload.initial_render_count;
          render();
        }});
      }});
    }}

    function renderEvidenceFilters() {{
      const filters = [
        ['all', t('evidenceAll')],
        ['high_signal', t('evidenceHighSignal')],
        ['peer_reviewed', t('evidencePeerReviewed')],
        ['preprint', t('evidencePreprints')],
      ];
      evidenceToolbar.innerHTML = filters.map(([key, label]) => {{
        return `<button class="filter-pill ${{activeEvidence === key ? 'is-active' : ''}}" data-evidence="${{escapeHtml(key)}}">${{escapeHtml(label)}} <span>${{evidenceCount(key)}}</span></button>`;
      }}).join('');
      evidenceToolbar.querySelectorAll('.filter-pill').forEach((button) => {{
        button.addEventListener('click', () => {{
          activeEvidence = button.dataset.evidence;
          visibleCount = payload.initial_render_count;
          render();
        }});
      }});
    }}

    function renderMonths() {{
      const options = [
        `<option value="all">${{escapeHtml(t('monthAll'))}}</option>`,
        ...payload.months.map((month) => {{
          const selected = month.value === activeMonth ? 'selected' : '';
              return `<option value="${{escapeHtml(month.value)}}" ${{selected}}>${{escapeHtml(monthLabel(month))}} (${{month.count}})</option>`;
        }}),
      ];
      monthSelect.innerHTML = options.join('');
      monthSelect.value = activeMonth;
    }}

    function renderSortSelect() {{
      const options = [
        ['best', t('sortBest')],
        ['newest', t('sortNewest')],
      ];
      sortSelect.innerHTML = options.map(([value, label]) => {{
        const selected = value === activeSort ? 'selected' : '';
        return `<option value="${{escapeHtml(value)}}" ${{selected}}>${{escapeHtml(label)}}</option>`;
      }}).join('');
      sortSelect.value = activeSort;
    }}

    function renderMissingSources() {{
      if (!payload.missing_sources.length) {{
        missingPanel.hidden = true;
        return;
      }}
      missingPanel.hidden = false;
      document.getElementById('missing-title').textContent = t('sourceUnavailableTitle');
      document.getElementById('missing-copy').textContent = t('sourceUnavailableCopy');
      missingList.innerHTML = payload.missing_sources.map((entry) => `<li>${{escapeHtml(entry)}}</li>`).join('');
    }}

    function renderCards() {{
      const items = filteredItems();
      const visibleItems = items.slice(0, visibleCount);
      if (!visibleItems.length) {{
        cardGrid.innerHTML = '';
        emptyState.hidden = false;
        emptyState.textContent = t('noItems');
      }} else {{
        emptyState.hidden = true;
        cardGrid.innerHTML = visibleItems.map((item) => {{
          const tags = (lang === 'en' ? item.matched_topics : item.matched_topics_zh)
            .slice(0, 4)
            .map((tag) => `<span class="tag">${{escapeHtml(tag)}}</span>`)
            .join('');
          const venueName = item.journal_name || t('venueUnknown');
          const metrics = [
            item.quality_label ? `<span class="metric-badge" data-quality="${{escapeHtml(item.quality_tier)}}">${{escapeHtml(lang === 'en' ? item.quality_label.en : item.quality_label.zh)}}</span>` : '',
            item.venue_type_label ? `<span class="metric-badge" data-quality="${{escapeHtml(item.quality_tier)}}">${{escapeHtml(lang === 'en' ? item.venue_type_label.en : item.venue_type_label.zh)}}</span>` : '',
            item.journal_quartile ? `<span class="metric-badge" data-quality="${{escapeHtml(item.quality_tier)}}">${{escapeHtml(item.journal_quartile)}}</span>` : '',
            item.impact_factor ? `<span class="metric-badge" data-quality="${{escapeHtml(item.quality_tier)}}">IF ${{escapeHtml(item.impact_factor)}}</span>` : '',
          ].join('');
          const summary = lang === 'en' ? item.card_summary_en : item.card_summary_zh;
          const whyImportant = lang === 'en' ? item.why_important : item.why_important_zh;
          return `
            <article class="intel-card" data-track="${{escapeHtml(item.track)}}" data-quality="${{escapeHtml(item.quality_tier)}}">
              <div class="card-meta">
                <span>${{escapeHtml(item.published_at)}}</span>
                <span>${{escapeHtml(trackLabel(item.track))}}</span>
              </div>
              <h3>${{escapeHtml(item.title_en)}}</h3>
              <div class="venue-row">
                <span class="venue-name">${{escapeHtml(venueName)}}</span>
                ${{metrics}}
              </div>
              <p class="signal-line">${{escapeHtml(t('signalLinePrefix'))}}: ${{escapeHtml(whyImportant)}}</p>
              <div class="card-meta">${{tags}}</div>
              <p class="summary">${{escapeHtml(summary)}}</p>
              <div class="card-footer">
                <div class="card-meta">
                  <span class="tag">${{escapeHtml(item.study_type)}}</span>
                  <span class="tag">${{escapeHtml(item.modality)}}</span>
                  <span class="tag">${{escapeHtml(item.source)}}</span>
                </div>
                <a class="source-link" href="${{escapeHtml(item.url)}}" target="_blank" rel="noreferrer">${{escapeHtml(t('openSource'))}}</a>
              </div>
            </article>`;
        }}).join('');
      }}
      const hasMore = items.length > visibleCount;
      loadMoreButton.hidden = !hasMore;
      sentinel.hidden = !hasMore;
      loadMoreButton.textContent = t('loadMore');
    }}

    function render() {{
      document.querySelectorAll('.toggle-button').forEach((button) => {{
        button.classList.toggle('is-active', button.dataset.lang === lang);
      }});
      renderSummary();
      renderFilters();
      renderEvidenceFilters();
      renderMonths();
      renderSortSelect();
      renderMissingSources();
      renderCards();
    }}

    document.querySelectorAll('.toggle-button').forEach((button) => {{
      button.addEventListener('click', () => {{
        lang = button.dataset.lang;
        render();
      }});
    }});

    monthSelect.addEventListener('change', () => {{
      activeMonth = monthSelect.value;
      visibleCount = payload.initial_render_count;
      renderCards();
    }});

    sortSelect.addEventListener('change', () => {{
      activeSort = sortSelect.value;
      visibleCount = payload.initial_render_count;
      renderCards();
    }});

    loadMoreButton.addEventListener('click', () => {{
      visibleCount += payload.initial_render_count;
      renderCards();
    }});

    const observer = new IntersectionObserver((entries) => {{
      entries.forEach((entry) => {{
        if (!entry.isIntersecting) {{
          return;
        }}
        const items = filteredItems();
        if (visibleCount < items.length) {{
          visibleCount += payload.initial_render_count;
          renderCards();
        }}
      }});
    }}, {{ rootMargin: '240px 0px' }});
    observer.observe(sentinel);

    render();
  </script>
</body>
</html>"""


def _serialize_item(item: IntelItem) -> dict:
    return {
        "id": item.id,
        "title_en": item.title_en,
        "source": item.source,
        "url": item.url,
        "track": item.track,
        "published_at": item.published_at.date().isoformat(),
        "month_key": item.published_at.strftime("%Y-%m"),
        "matched_topics": item.matched_topics,
        "matched_topics_zh": item.matched_topics_zh,
        "study_type": item.study_type,
        "modality": item.modality,
        "card_summary_en": item.card_summary_en,
        "card_summary_zh": item.card_summary_zh,
        "why_important": item.why_important,
        "why_important_zh": item.why_important_zh,
        "summary_en": item.summary_en,
        "summary_zh": item.summary_zh,
        "journal_name": item.journal_name,
        "journal_quartile": item.journal_quartile,
        "impact_factor": item.impact_factor,
        "venue_type": item.venue_type,
        "quality_tier": item.quality_tier,
        "importance_score": item.importance_score,
        "venue_type_label": _venue_type_label(item.venue_type),
        "quality_label": _quality_label(item.quality_tier),
    }


def _build_month_options(items: list[IntelItem]) -> list[dict]:
    counts: dict[str, int] = {}
    for item in items:
        key = item.published_at.strftime("%Y-%m")
        counts[key] = counts.get(key, 0) + 1
    months = []
    for key in sorted(counts.keys(), reverse=True):
        year, month = key.split("-")
        month_number = int(month)
        months.append(
            {
                "value": key,
                "count": counts[key],
                "label_en": f"{_month_name(month_number)} {year}",
                "label_zh": f"{year}年{month_number}月",
            }
        )
    return months


def _month_name(month_number: int) -> str:
    names = [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ]
    return names[month_number - 1]


def _venue_type_label(venue_type: str) -> dict[str, str] | None:
    if venue_type == "journal":
        return {"en": "Journal", "zh": "期刊"}
    if venue_type == "preprint":
        return {"en": "Preprint", "zh": "预印本"}
    if venue_type == "institution":
        return {"en": "Institution", "zh": "机构"}
    if venue_type:
        return {"en": venue_type.title(), "zh": venue_type}
    return None


def _quality_label(quality_tier: str) -> dict[str, str] | None:
    if quality_tier == "high":
        return {"en": "High-trust", "zh": "高信号"}
    if quality_tier == "solid":
        return {"en": "Solid journal", "zh": "稳健期刊"}
    if quality_tier == "standard":
        return {"en": "Trusted journal", "zh": "可信期刊"}
    if quality_tier == "preprint":
        return {"en": "Preprint", "zh": "预印本"}
    if quality_tier == "institution":
        return {"en": "Institution", "zh": "机构"}
    if quality_tier:
        return {"en": quality_tier.title(), "zh": quality_tier}
    return None
