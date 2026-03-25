# Intelligence Center

Research-first, shareable web archive for `psychiatry / psychology + AI`.

## What It Does

- Pulls items from public, stable sources defined in [`config/sources.yml`](/Users/zhaoxuhang/Desktop/情报中心/config/sources.yml).
- Scores and ranks items for three tracks:
  - `frontier_core`
  - `current_program`
  - `enabling_signals`
- Generates:
  - a local dashboard in `intel/site/index.html`
  - a machine-readable snapshot in `intel/site/latest.json`
  - an English-default card feed with an in-page English / Chinese language switch

## Current Product Shape

- Focus window: last 12 months of psychiatry / psychology + AI research.
- Default view: English.
- Alternate view: Chinese labels and card summaries via in-page toggle.
- Presentation: scrollable research cards with progressive loading and month selection.
- Scope: research and enabling signals only; market coverage is intentionally omitted.
- Card structure: title, venue, quality badges, concise summary, direct link.

## Run

```bash
python3 scripts/run_morning_brief.py --date 2026-03-23 --open
```

Useful flags:

- `--dry-run`: render the dashboard to stdout without writing files.
- `--fixture-bundle tests/fixtures/mock_bundle.json`: run against local mock data.
- `--skip-open`: do not open the generated dashboard.
- `--github-pages`: also export the generated site into `docs/` for GitHub Pages.
- `--llm`: manually invoke OpenAI to enhance the executive summary only.
- `--llm-model gpt-4o-mini`: override the manual enhancement model.

Manual LLM enhancement requires:

```bash
export OPENAI_API_KEY=...
python3 scripts/run_morning_brief.py --open --llm
```

## GitHub Pages

Generate and export the publishable site:

```bash
python3 scripts/run_morning_brief.py --github-pages --skip-open
```

That command writes:

- local working site: `intel/site/index.html` and `intel/site/latest.json`
- GitHub Pages publish directory: `docs/index.html` and `docs/latest.json`

To share it publicly:

1. Create a GitHub repository and upload this project.
2. In GitHub, open `Settings` -> `Pages`.
3. Set the source to `Deploy from a branch`.
4. Choose your default branch and the `/docs` folder.
5. Save. GitHub will publish the site and give you a public URL.

The `docs/.nojekyll` file is created automatically so Pages serves the static files directly.

## Structure

- `config/`: topic tree and source catalog
- `config/journals.yml`: optional local venue metadata such as quartile / IF
- `src/intel_center/`: pipeline, parsing, scoring, rendering
- `scripts/`: CLI entrypoint
- `intel/`: generated dashboard and snapshots
- `state/`: machine state and latest run snapshot
- `tests/`: parser and end-to-end coverage with fixtures

## Notes

- Config files use JSON-compatible YAML so they can be parsed without extra dependencies.
- Live fetching uses only the Python standard library.
- Manual OpenAI enhancement uses the Responses API with `text.format: {type: "json_object"}` and falls back to local heuristics if the API request fails after the run starts.
- If a feed fails, the run still completes and marks the missing source section in the page.
- The dashboard is intentionally narrow and sparse when high-signal research items are missing; it will not fill the page with generic AI news.
