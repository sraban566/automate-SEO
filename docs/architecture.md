# How automate-SEO works

automate-SEO separates fetching, extraction and interpretation. Each stage keeps enough evidence to explain the next one.

```mermaid
flowchart LR
  A[Seed URL and settings] --> B[Robots and sitemaps]
  B --> C[SQLite URL queue]
  C --> D[Scoped HTTP fetch]
  D --> E[HTML extraction]
  E --> F{Browser needed?}
  F -->|Yes| G[Chromium, waits and scrolling]
  F -->|No| H[Page record]
  G --> H
  H --> C
  H --> I[Readable documents and evidence]
  I --> J[SEO observations and playbooks]
```

## Source layout

| Path | Responsibility |
|---|---|
| `src/automate_seo/cli.py` | Commands, input validation and output lock |
| `src/automate_seo/network.py` | Configuration, normalized URLs, HTTP transport, pacing and robots rules |
| `src/automate_seo/engine.py` | Sitemap discovery, SQLite queue, processing and exports |
| `src/automate_seo/render.py` | Local browser, dependency routing, readiness, scrolling and screenshots |
| `src/automate_seo/extract.py` | HTML observations, text selection and page finding candidates |
| `src/automate_seo/content.py` | Common HTML structures converted to Markdown |
| `src/automate_seo/diagnostics.py` | Access classification and stop reasons |
| `src/automate_seo/evidence.py` | Shared representation selection, raw noindex preservation and missing-content candidates |
| `src/automate_seo/backlinks.py` | Source verification, late-link rendering and bounded redirect follow-ups |
| `src/automate_seo/reputation.py` | Search plans/imports, source review rubric, grouping and estimated reputation reports |
| `src/automate_seo/discovery.py` | Budgeted provider adapters, host/saved imports, provenance, deduplication and cache |
| `src/automate_seo/research.py` | Evidence packets, validated profile reviews, query generation and competitor gates |
| `src/automate_seo/findings.py` | Evidence, business impact and acceptance contracts for audit findings |
| `src/automate_seo/doctor.py` | Separate runtime and optional target/search probes |
| `src/automate_seo/reports.py` | Offline branded HTML/PDF presentation of reviewed content and native audit findings |
| `src/automate_seo/report_assets/` | Packaged product logo and self-contained report styles |
| `playbooks/` | SEO methods, templates, examples and industry guidance |
| `tests/` | Local HTTP/browser fixtures and regression cases |

HTTP workers fetch pages concurrently, with shared per-origin request spacing. Results are consumed in queue order. Browser observations run sequentially in fresh contexts; sessions and cookies do not cross pages. Browser HTTP requests are routed through the same checked transport. This makes request behavior inspectable, but differs from Chromium's ordinary direct network stack and does not replicate a normal browser's TLS fingerprint.

The SQLite database stores configuration, discovered URLs, page records and sitemap membership. A run can recover its pending queue. It does not implement cross-run incremental refresh. Reports and content exports are regenerated from stored records without fetching a website again.

## Python usage

```python
from automate-seo import Config, Crawler

config = Config(
    url="https://example.com",
    max_pages=10,
    render_mode="auto",
    timeout=40,
)
crawler = Crawler(config, "runs/python-example")
try:
    summary = crawler.run()
    print(summary["html_documents"])
finally:
    crawler.close()
```

Use one `Crawler` per output directory and always close it. The CLI provides an output lock; direct library users must avoid concurrent writers to the same directory. The public classes are a small Python interface, not an asynchronous hosted API.

## Interpretation boundaries

A page observation establishes what this run received. It does not establish what a search engine indexed or what a user saw on a different device, geography, session or date. SEO findings remain evidence-bearing candidates until interpreted in context. Read the [measurement boundaries](../references/measurement-boundaries.md) before turning them into business conclusions.
