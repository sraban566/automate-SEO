"""Run bounded SEO and AI-search health checks or replay saved evidence."""

from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from .engine import Crawler
from .evidence import capture_quality, selected_data
from .extract import extract
from .llms_files import validate_document
from .network import Config, normalize_url, origin, utcnow
from .review import read_json, read_pages
from .specialists import (
    backlink_digital_pr_strategist,
    competitor_entity_gap_researcher,
    content_schema_architect,
    issue,
    technical_auditor,
)


def inspect_resources(crawler, pages):
    """Probe same-scope canonical targets and the two public AI guidance files."""

    def fetch(url):
        return crawler.transport.fetch(url, crawler.robots.allowed, crawler.robots.delay)

    inspected = {normalize_url(url) for p in pages for url in (p["url"], p.get("final_url")) if url}
    canonical_urls = set()
    for page in pages:
        data, _ = selected_data(page)
        for link in data.get("canonical", []):
            target = link.get("url")
            if (
                target
                and normalize_url(target) not in inspected
                and crawler.config.in_scope(target)
            ):
                canonical_urls.add(target)
    checks = {}
    for url in sorted(canonical_urls)[:15]:
        response = fetch(url)
        data = {}
        if not response.error and 200 <= response.status < 300:
            data = extract(response.body, response.final_url, response.headers)
        checks[normalize_url(url)] = dict(
            url=url,
            final_url=response.final_url,
            status=response.status,
            error=response.error,
            headers=response.headers,
            redirects=response.redirects,
            data=data,
        )
    seed = crawler.config.url
    page = next((p for p in pages if p["url"] == seed), {})
    root = origin(page.get("final_url") or seed)
    files = []
    for name in ("llms.txt", "llms-full.txt"):
        url = f"{root}/{name}"
        response = fetch(url)
        row = dict(url=url, status=response.status, error=response.error, validation=None)
        if response.status == 200 and not response.error:
            try:
                value = response.body.decode("utf-8-sig")
                row["validation"] = validate_document(
                    value, response.final_url, name == "llms-full.txt"
                )
            except UnicodeDecodeError:
                row["validation"] = {
                    "valid": False,
                    "errors": ["File is not valid UTF-8."],
                    "links": [],
                    "warnings": [],
                }
        files.append(row)
    return checks, files


def health_report(
    seed,
    pages,
    robots,
    canonical_checks=None,
    llms=None,
    competitors=(),
    reputation=None,
    discovery=None,
):
    technical = technical_auditor(pages, robots, canonical_checks)
    content = content_schema_architect(pages)
    results = dict(
        technical_auditor=technical,
        content_schema_architect=content,
        competitor_entity_gap_researcher=competitor_entity_gap_researcher(
            content["pages"], competitors
        ),
        backlink_digital_pr_strategist=backlink_digital_pr_strategist(reputation),
    )
    findings = technical["findings"] + content["findings"]
    if not pages:
        findings.append(
            issue(
                "empty_crawl",
                seed,
                "critical",
                "No page attempts were recorded.",
                "Check crawl scope and runtime before interpreting site health.",
            )
        )
    for row in llms or []:
        validation = row["validation"]
        if row["error"] or row["status"] != 200:
            missing = row["status"] in (404, 410) and not row["error"]
            findings.append(
                issue(
                    "llms_missing" if missing else "llms_unverified",
                    row["url"],
                    "info" if missing else "warning",
                    f"HTTP {row['status']}; {row['error'] or 'file unavailable'}.",
                    "Publish a reviewed file if it suits the site, and verify the public response.",
                )
            )
        elif validation and not validation["valid"]:
            findings.append(
                issue(
                    "llms_invalid",
                    row["url"],
                    "warning",
                    " ".join(validation["errors"]),
                    "Correct the guidance document and verify the public response.",
                )
            )
    unique = {(f["code"], f["url"], f["subject"]): f for f in findings}
    findings = sorted(
        unique.values(),
        key=lambda f: (-f["impact"], -f["priority"], f["code"], f["url"], f["subject"]),
    )
    measured = [
        p["citation_readiness"]["score"]
        for p in content["pages"]
        if p["citation_readiness"]["score"] is not None
    ]
    return dict(
        format_version=1,
        target=seed,
        generated_at=utcnow(),
        coverage=dict(
            attempted=len(pages),
            interpretable=sum(capture_quality(p)["absence_supported"] for p in pages),
            readiness_scored=len(measured),
        ),
        citation_readiness_average=round(sum(measured) / len(measured), 1) if measured else None,
        measured_ai_share_of_voice=None,
        stages=results,
        llms=llms,
        discovery=discovery,
        findings=findings,
    )


def research_sources(queries, seed, brand, out):
    """Collect leads and verify a small sample using the native research pipeline."""
    from .discovery import discover
    from .reputation import reputation

    folder = Path(out) / "discovery"
    if folder.exists() and any(folder.iterdir()):
        raise ValueError("Choose a fresh output directory for discovery.")
    discovered = discover(
        queries,
        folder,
        target=seed,
        max_requests=16,
        max_queries=8,
        max_candidates=40,
        timeout=8,
        seconds=75,
    )
    findings = None
    if discovered["candidate_count"]:
        findings = reputation(
            folder / "sources.csv",
            seed,
            brand,
            Path(out) / "reputation",
            limit=6,
            mode="http",
            requested_search_pages=1,
        )
    return {
        "status": discovered["status"],
        "search_available": discovered["search_available"],
        "native_requests": discovered["native_requests"],
        "leads": discovered["candidates"],
        "evidence": "discovery/discovery.json",
    }, findings


def run_audit(
    *,
    url=None,
    crawl=None,
    out=None,
    max_pages=10,
    mode="http",
    allow_private=False,
    competitors=(),
    reputation=None,
    research_queries=None,
    brand=None,
):
    if (url is None) == (crawl is None):
        raise ValueError("Choose exactly one URL or saved crawl.")
    if research_queries is not None and (
        reputation is not None or not brand or not brand.strip() or out is None
    ):
        raise ValueError(
            "Research queries require --brand and a new output folder. Supply either queries or saved reputation evidence."
        )
    reputation_data = read_json(reputation) if reputation is not None else None
    checks = llms = None
    if url is not None:
        if out is None or not 1 <= max_pages <= 50:
            raise ValueError("Live audits require an output directory and a 1–50 page budget.")
        from .cli import OutputLock

        config = Config(
            url,
            max_pages=max_pages,
            render_mode=mode,
            include_www=True,
            allow_private=allow_private,
            max_sitemaps=5,
            max_depth=3,
            max_discovered=1000,
            timeout=10,
            retries=0,
            save_html=False,
            selection="priority",
        )
        crawl = Path(out) / "crawl"
        with OutputLock(crawl), closing(Crawler(config, crawl)) as crawler:
            crawler.run()
            pages = read_pages(crawl)
            checks, llms = inspect_resources(crawler, pages)
            robots = crawler.robots.evidence
    else:
        crawl = Path(crawl)
        pages = read_pages(crawl)
        robots = read_json(crawl / "robots.json")
    seed = read_json(crawl / "summary.json")["seed"]
    discovery = None
    if research_queries is not None:
        discovery, reputation_data = research_sources(read_json(research_queries), seed, brand, out)
    if reputation_data is not None:
        observed_host = urlsplit(reputation_data.get("target", "")).hostname
        target_host = urlsplit(seed).hostname
        if not observed_host or observed_host.removeprefix("www.") != target_host.removeprefix(
            "www."
        ):
            raise ValueError("Reputation evidence belongs to a different target.")
    return health_report(seed, pages, robots, checks, llms, competitors, reputation_data, discovery)


def evaluate_gate(report, level, baseline=None):
    severity = {"info": 0, "warning": 1, "critical": 2}
    if baseline is not None and (
        baseline.get("format_version") != report["format_version"]
        or baseline.get("target") != report["target"]
    ):
        raise ValueError("Baseline must use the same report format and target.")
    previous = {
        (f["code"], f["url"], f["subject"]): severity[f["severity"]]
        for f in (baseline or {}).get("findings", [])
    }
    increases = [
        f
        for f in report["findings"]
        if severity[f["severity"]] > previous.get((f["code"], f["url"], f["subject"]), -1)
    ]
    return {
        "threshold": level,
        "baseline_used": baseline is not None,
        "new_or_escalated": len(increases),
        "failed": level != "none"
        and any(severity[f["severity"]] >= severity[level] for f in increases),
    }
