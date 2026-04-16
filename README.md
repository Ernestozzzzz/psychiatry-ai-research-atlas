# Intelligence Center

Research-first, shareable web archive for `psychiatry / psychology + AI`.

## What It Does

- Pulls items from public, stable sources defined in [`config/sources.yml`](/Users/zhaoxuhang/Desktop/情报中心/config/sources.yml).
- Evaluates external `skills / archives / databases / products` with a reusable admission rubric from [`config/candidates.yml`](/Users/zhaoxuhang/Desktop/情报中心/config/candidates.yml).
- Scores and ranks items for three tracks:
  - `frontier_core`
  - `current_program`
  - `enabling_signals`
- Splits output into two layers:
  - `curated brief`: high-trust items that clear promotion rules
  - `raw intake`: newly captured items that still need review or stronger corroboration
- Generates:
  - a local dashboard in `intel/site/index.html`
  - a machine-readable snapshot in `intel/site/latest.json`
  - an English-default dashboard with in-page English / Chinese language switch
  - an admission report for source / archive / skill candidates

## Current Product Shape

- Focus window: last 12 months of psychiatry / psychology + AI research.
- Delivery mode: weekly brief first, daily escalation later only after archive noise is under control.
- Default view: English.
- Alternate view: Chinese labels and card summaries via in-page toggle.
- Presentation: scrollable curated research cards, a raw-intake review section, and an admission report section.
- Scope: research and enabling signals only; market coverage is intentionally omitted.
- Card structure: title, venue, quality badges, concise summary, direct link, and screening notes.

## Source Strategy

- Backbone sources: `PubMed / Europe PMC`
- Archive sources: `arXiv`, `PsyArXiv`
- Specialty source: `NeuroBlu publications`
- Promotion layer: local psychiatry / mental health journal whitelist in [`config/journals.yml`](/Users/zhaoxuhang/Desktop/情报中心/config/journals.yml)

Archive items can enter `raw intake`, but they do not enter the curated brief unless they satisfy an explicit promotion rule such as strong study type, corroboration, or top-journal linkage.

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
- `config/candidates.yml`: admission registry and rubric for external skills / archives / sources
- `config/journals.yml`: optional local venue metadata such as quartile / IF
- `src/intel_center/`: pipeline, parsing, scoring, rendering
- `src/intel_center/weixin_bridge.py`: WeChat <-> Codex bridge with optional voice transcription
- `scripts/`: CLI entrypoint
- `intel/`: generated dashboard and snapshots
- `state/`: machine state and latest run snapshot
- `tests/`: parser and end-to-end coverage with fixtures

## WeChat Bridge

The repository now includes a WeChat bridge that reuses the `openclaw-weixin` protocol shape but calls the local `codex` CLI as the backend agent.

Current scope:

- QR login against the Weixin bot endpoints
- long-poll `getupdates`
- per-user `context_token` persistence
- per-user Codex `thread_id` persistence
- optional inbound voice transcription, with offline `faster-whisper` as the default backend
- text-only outbound replies through `sendmessage`

Current non-goals:

- media upload
- group chats
- typing indicator
- production hardening for multi-account routing

Performance defaults:

- default model: `gpt-5.4-mini`
- default reasoning effort: `medium`
- default disabled Codex features for bridge replies: `plugins`, `shell_snapshot`
- non-ASCII workspaces are mirrored into an ASCII path under `~/.codex/weixin-bridge/mirrors/` before each Codex call

That mirror avoids the current Codex websocket regression on non-ASCII workspace paths and keeps WeChat replies much faster than running directly inside a Chinese-named project directory.

### Login

```bash
python3 scripts/run_weixin_bridge.py login
```

The command prints a QR URL. Scan it in WeChat and wait for confirmation. Credentials and peer-thread mappings are stored by default at:

```bash
~/.codex/weixin-bridge/state.json
```

### Run Once

```bash
python3 scripts/run_weixin_bridge.py once --workspace /absolute/path/to/workspace
```

This fetches one batch of inbound messages, forwards text messages into Codex, and sends the final reply back to WeChat. If a supported voice attachment is present, the bridge will try to transcribe it first and append the transcript to the Codex prompt.

### Run Continuously

```bash
python3 scripts/run_weixin_bridge.py serve --workspace /absolute/path/to/workspace
```

Useful options:

- `--codex-sandbox read-only|workspace-write|danger-full-access`
- `--codex-model <model>`
- `--codex-reasoning-effort <minimal|low|medium|high|xhigh>`
- `--allow-from <user@im.wechat>`
- `--state-path <path>`
- `--preamble "custom prompt prefix"`
- `--no-optimize-latency`: disable the ASCII mirror if you explicitly want direct workspace execution
- `--disable-audio-transcription`: ignore inbound WeChat voice attachments
- `--transcription-backend local|openai`: choose the offline or API transcription path
- `--transcribe-cli <path>`: override the local transcription CLI
- `--transcribe-model <model>`: override the voice transcription model
- `--transcribe-language <lang>`: language hint for voice transcription, defaults to `zh`

The recommended first production setup is `--codex-sandbox read-only`, then move to `workspace-write` only after you are comfortable with the safety model.

Offline voice transcription requirements:

```bash
brew install ffmpeg
python3 -m pip install faster-whisper
```

The default bridge mode is offline:

```bash
python3 scripts/run_weixin_bridge.py serve --workspace /absolute/path/to/workspace
```

That uses `faster-whisper` locally and does not require `OPENAI_API_KEY`.

OpenAI API transcription remains optional:

```bash
export OPENAI_API_KEY=...
```

If you explicitly switch to `--transcription-backend openai`, the bridge looks for the CLI at:

```bash
~/.codex/skills/transcribe/scripts/transcribe_diarize.py
```

You can override that path with `--transcribe-cli` or `TRANSCRIBE_CLI`.

### Daily WeChat Push

Generate the latest brief and push it to the known WeChat peer(s):

```bash
python3 scripts/push_weixin_brief.py
```

Useful options:

- `--print-only`: generate the outgoing message without sending it
- `--to-user <user@im.wechat>`: push only to a specific peer
- `--max-curated 5`
- `--max-raw 3`
- `--llm`: optionally enhance the summary bullets before pushing

### Background Service

Start the bridge as a macOS `launchd` agent:

```bash
python3 scripts/run_weixin_bridge.py service-start --workspace /absolute/path/to/workspace
```

Control commands:

```bash
python3 scripts/run_weixin_bridge.py service-status --workspace /absolute/path/to/workspace
python3 scripts/run_weixin_bridge.py service-stop --workspace /absolute/path/to/workspace
python3 scripts/run_weixin_bridge.py service-restart --workspace /absolute/path/to/workspace
python3 scripts/run_weixin_bridge.py service-logs --workspace /absolute/path/to/workspace
python3 scripts/run_weixin_bridge.py health-check --workspace /absolute/path/to/workspace
```

The service writes:

- launch agent plist: `~/Library/LaunchAgents/dev.codex.weixin-bridge.<hash>.plist`
- stdout / stderr logs: `~/.codex/weixin-bridge/logs/`
- optimized Codex mirror: `~/.codex/weixin-bridge/mirrors/`

## Notes

- Config files use JSON-compatible YAML so they can be parsed without extra dependencies.
- Live fetching uses only the Python standard library.
- Manual OpenAI enhancement uses the Responses API with `text.format: {type: "json_object"}` and falls back to local heuristics if the API request fails after the run starts.
- If a feed fails, the run still completes and marks the missing source section in the page.
- The dashboard is intentionally narrow and sparse when high-signal research items are missing; it will not fill the page with generic AI news.
