# automate-SEO

A local SEO, Answer Engine Optimization (AEO), and Generative Engine Optimization (GEO) audit engine. Run it from a terminal, GitHub Actions, or a coding agent with shell access.

## Quickstart

Requires Python 3.10 or newer. The HTTP crawler and health checks run without API keys.

```sh
git clone https://github.com/sraban566/automate-SEO.git
cd automate-SEO
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell uses .\.venv\Scripts\Activate.ps1
python -m pip install -e .
automate-seo doctor
automate-seo health --url https://example.com --out runs/example --max-pages 5
```

With uv, run `uv sync` and prefix commands with `uv run`. For JavaScript-rendered pages, run `uv sync --extra browser` and `uv run playwright install chromium`, then pass `--mode auto` or `--mode browser` to the audit. With pip, install `-e ".[browser]"` and run `python -m playwright install chromium`. You can also invoke any command via `python scripts/run.py` with an installed runtime.

Use a fresh output directory for each live health audit. The preserved `crawl --resume` interface continues an existing crawl using its SQLite frontier. `health --crawl` replays a saved snapshot offline.

## Architecture

1. `scripts/run.py` chooses the configured Python environment and calls the CLI. The crawler discovers URLs from robots, sitemaps, and observed links, then queues them in SQLite.
2. Bounded HTTP workers fetch pages. Optional Chromium renders JavaScript under the same transport constraints. The crawler retains raw and rendered observations, coverage, and the source links for findings.
3. Four composable stages consume the evidence. The Technical Auditor checks availability, canonical destinations, page metadata, and crawler rules. The Content & Schema Architect checks extractable answers and JSON-LD. The Competitor & Entity Gap Researcher finds schema comparisons in supplied competitor crawls. The Backlink & Digital PR Strategist uses verified reputation evidence and flags missing inputs.
4. The report writer emits `health.json`, `health.md`, and offline `health.html`. The findings include estimated impact and effort, plus an action tied to the observed page.

See the [upstream architecture audit](docs/upstream-audit.md), [module map](docs/architecture.md), and [command and integration guides](docs/README.md). The legacy Python module name `beyondseo` is retained for API compatibility.

## AI-search checks

- **AI guidance files.** Generate `llms.txt` and a bounded `llms-full.txt` companion from reviewed crawl captures. Validation checks Markdown structure and link syntax. The live health check also probes the public files. The llms.txt format is a proposal for agent-readable guidance. Provider inclusion depends on each search system. [llms.txt proposal](https://llmstxt.org/), [Google AI search guidance](https://developers.google.com/search/docs/appearance/ai-features)
- **Entity graphs.** Validate nested JSON-LD types, repeated `@id` declarations, same-document references, `sameAs` links, and fields for Organization, FAQPage, HowTo, Article and Product. The offline checks cover a defined subset of Schema.org and leave factual verification and search-feature eligibility to review. [Schema.org vocabulary](https://schema.org/)
- **Citation readiness.** Score visible concise answers, semantic HTML, factual markers, and citation hooks from 0 to 100. Scores estimate extractability. Actual AI citations and share of voice require dated samples from answer engines. Snapshots created before the new DOM signals require a fresh crawl for scoring.
- **Robots policy.** Report the declared search rules for OAI-SearchBot, Claude-SearchBot, PerplexityBot and Googlebot, with separate fields for training, product controls, and user-activated agents. An allow rule describes the captured policy. It does not establish provider access through a CDN or actual indexing. [OpenAI crawlers](https://developers.openai.com/api/docs/bots), [Anthropic crawlers](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler), [Perplexity crawlers](https://docs.perplexity.ai/docs/resources/perplexity-crawlers), [Google crawlers](https://developers.google.com/crawling/docs/crawlers-fetchers/overview-google-crawlers)

```sh
automate-seo health --crawl runs/example/crawl --out runs/review --fail-on none
automate-seo entity-review --crawl runs/example/crawl --out runs/entities
automate-seo llms generate --crawl runs/example/crawl --out runs/llms
automate-seo llms validate runs/llms/llms.txt --url https://example.com/llms.txt
automate-seo llms validate runs/llms/llms-full.txt --url https://example.com/llms-full.txt --full
```

Pass `--competitor-crawl` once per previously captured competitor and optionally `--reputation path/to/reputation.json`. Use `--research-queries queries.json --brand "Your Brand"` to run bounded native search and verify up to six source pages as part of the audit. The query file uses the [reviewed discovery format](docs/discovery-and-competitors.md). Returned URLs are leads and each verified result retains its source observations. The [backlink catalog](docs/backlink-source-catalog.md) provides additional outreach leads.

## Agents and Git patches

Use `automate-seo agent tools.list` for the available tools. The CLI and newline-delimited JSON-RPC interface expose the same local tool contracts. These commands are ready to call from Claude Code, Codex, Cursor, or a framework wrapper with terminal access.

| Tool | Named parameters |
| --- | --- |
| `audit` | `crawl_dir` |
| `content-brief` | `crawl_dir`, `url`, optional `question` |
| `schema-fix` | `repo`, `file`, `changes`, optional `framework` |

For the CLI, save the named parameters as a JSON object and run `automate-seo agent content-brief --input brief.json`. For a subprocess integration, start `automate-seo rpc` and send one UTF-8 JSON-RPC request per line. Read one JSON response for each request with an ID. The stdio server implements this local JSON-RPC contract and can sit behind an MCP adapter.

```json
{"jsonrpc":"2.0","id":1,"method":"audit","params":{"crawl_dir":"runs/example/crawl"}}
```

`schema-fix` accepts explicitly reviewed `title`, `description`, `canonical`, and `schema` values. Choose `html`, `next`, or `react` for a clean tracked source file relative to the Git root. HTML edits target the head. For Next.js App Router metadata, place a standalone `// automate-seo:metadata` line at module scope of a server route file. For React 19 metadata, place a standalone JSX comment `{/* automate-seo:metadata */}` in the page component. For JSON-LD, place `{/* automate-seo:schema */}` in the intended component. Existing structured data requires a deliberate merge. The tool returns an apply-checked diff and a fingerprint. Review the diff and run the site's own build in a review branch before deploying.

Example prompts:

- **Claude Code:** “Run `automate-seo health` on my staging site. Summarize the three highest-impact findings and the URL evidence.”
- **Codex:** “Call `automate-seo agent schema-fix` with these approved values. Show me the Next.js Git diff and describe the verification steps.”
- **Cursor:** “Use `automate-seo agent content-brief` to plan an answer-first update from the saved crawl. Cite the supplied primary sources for factual claims.”

## GitHub Actions

The [SEO workflow](https://github.com/sraban566/automate-SEO/blob/main/.github/workflows/seo-audit.yml) runs on pushes to `main`, weekly on Monday at 04:23 UTC, and on manual dispatch. Configure the repository variable `SEO_AUDIT_URLS` with one public URL per line, up to ten. The job renders each page in Chromium with first-party assets, a 45-second fetch and render timeout, and a ten-page budget per target. It uploads the three report formats even when the gate fails. Use public URLs and content suitable for inclusion in GitHub workflow reports.

`SEO_FAIL_ON` defaults to `critical`. Choose `warning` to also fail for metadata or schema warnings, or `none` for reporting only. Zero scorable pages raises a critical coverage finding and fails either active gate even when the baseline also had zero coverage. Optionally commit a reviewed `health.json` as `.github/seo-baselines/target-1.json` and repeat by target position. With a baseline, other findings fail the gate when they are new or have increased to the selected threshold. Stable existing findings still appear in the report.

## Development

```sh
python -m pip install -e ".[browser,dev,hosting-test]"
python -m playwright install chromium
ruff check .
ruff format --check .
python -m unittest discover -s tests -v
python scripts/check_docs.py
python scripts/validate_skill.py
automate-seo doctor
```

MIT licensed. Original copyright and source attribution are in [LICENSE](LICENSE) and [NOTICE](NOTICE).
