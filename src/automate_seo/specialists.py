"""Specialist stages over saved crawl and research evidence."""

from collections import Counter
from pathlib import Path

from .citation import score
from .entity_graph import graph_audit
from .evidence import capture_quality, combined_index_signals, selected_data
from .network import RobotsRules, normalize_url, origin
from .review import read_json, read_pages

SEARCH_AGENTS = ("OAI-SearchBot", "Claude-SearchBot", "PerplexityBot", "Googlebot")
OTHER_AGENTS = {
    "GPTBot": "training",
    "ChatGPT-User": "user_activated",
    "ClaudeBot": "training",
    "Claude-User": "user_activated",
    "Perplexity-User": "user_activated",
    "Google-Extended": "product_control",
}


def issue(code, url, severity, detail, action, effort=1, subject=""):
    impact = {"critical": 5, "warning": 3, "info": 1}[severity]
    return dict(
        code=code,
        url=url,
        severity=severity,
        detail=detail,
        action=action,
        impact=impact,
        effort=effort,
        priority=round(impact / effort, 2),
        subject=subject,
    )


def technical_auditor(pages, robots, canonical_checks=None):
    checks = canonical_checks or {}
    findings = []
    access = []
    by_url = {}
    for page in pages:
        for url in (page["url"], page.get("final_url")):
            if url:
                by_url[normalize_url(url)] = page
    policies = {}
    for page in pages:
        url = page.get("final_url") or page["url"]
        robots_record = robots.get(origin(url), {})
        known = not robots_record.get("blocked") and robots_record.get("status") in (200, 404, 410)
        known = known and not robots_record.get("text_truncated")
        for agent in (*SEARCH_AGENTS, *OTHER_AGENTS):
            key = (origin(url), agent)
            if known and key not in policies:
                policies[key] = RobotsRules(robots_record.get("text", ""), agent=agent)
            allowed = policies[key].allowed(url) if known else None
            access.append(
                dict(
                    url=url,
                    agent=agent,
                    purpose=OTHER_AGENTS.get(agent, "search"),
                    allowed=allowed,
                    robots_status=robots_record.get("status"),
                )
            )
            if allowed is False and agent in SEARCH_AGENTS:
                findings.append(
                    issue(
                        "search_bot_blocked",
                        url,
                        "critical",
                        f"Published robots.txt disallows {agent} at this URL.",
                        "Confirm the intended search policy and update the exact rule if appropriate.",
                        subject=agent,
                    )
                )
        if not known:
            findings.append(
                issue(
                    "robots_unverified",
                    url,
                    "warning",
                    "The robots policy could not be established from this capture.",
                    "Verify the robots.txt response from this origin.",
                )
            )
        quality = capture_quality(page)
        if not quality["usable"]:
            status = page.get("status", 0)
            sev = "critical" if 400 <= status and status not in {401, 403, 429} else "warning"
            findings.append(
                issue(
                    "page_unavailable",
                    url,
                    sev,
                    f"HTTP {status}; {page.get('error') or 'no usable HTML capture'}.",
                    "Verify serving, access and redirects for the affected URL.",
                )
            )
            continue
        data, _ = selected_data(page)
        if quality["absence_supported"]:
            for key, label in (("title", "page title"), ("meta_description", "meta description")):
                if not data.get(key, "").strip():
                    findings.append(
                        issue(
                            f"missing_{key}",
                            url,
                            "warning",
                            f"Missing {label}.",
                            "Add page-specific metadata and verify the rendered head.",
                        )
                    )
        for canonical in data.get("canonical", []):
            target = normalize_url(canonical.get("url"))
            if not target:
                findings.append(
                    issue(
                        "invalid_canonical",
                        url,
                        "critical",
                        "A declared canonical has no usable HTTP(S) target.",
                        "Set a valid canonical URL for the intended final page.",
                    )
                )
                continue
            destination = by_url.get(target) or checks.get(target)
            if not destination:
                findings.append(
                    issue(
                        "canonical_unverified",
                        url,
                        "warning",
                        f"Canonical target was outside the inspected evidence: {target}",
                        "Verify canonical destination status and indexability.",
                        subject=target,
                    )
                )
                continue
            status = destination.get("status", 0)
            signals = combined_index_signals(destination)
            noindex = signals["googlebot_noindex_observed"]
            if status in (404, 410) or (status >= 500 and not destination.get("error")) or noindex:
                observation = (
                    f"returned HTTP {status}"
                    if not noindex
                    else f"declares noindex (HTTP {status})"
                )
                findings.append(
                    issue(
                        "broken_canonical",
                        url,
                        "critical",
                        f"Canonical target {target} {observation}.",
                        "Restore the preferred URL or point directly to an indexable replacement.",
                        subject=target,
                    )
                )
            elif destination.get("error") or status >= 400 or destination.get("redirects"):
                findings.append(
                    issue(
                        "canonical_destination_review",
                        url,
                        "warning",
                        f"Canonical target {target} redirected or was unable to be confirmed.",
                        "Check the final canonical response and prefer a direct URL.",
                        subject=target,
                    )
                )
        if len(data.get("canonical", [])) > 1:
            findings.append(
                issue(
                    "multiple_canonicals",
                    url,
                    "warning",
                    "More than one canonical declaration was observed.",
                    "Keep one canonical declaration for the chosen URL.",
                )
            )
    return {
        "status": "complete" if pages else "insufficient_evidence",
        "findings": findings,
        "robots_access": access,
    }


def content_schema_architect(pages):
    content = []
    findings = []
    for page in pages:
        quality = capture_quality(page)
        data, _ = selected_data(page)
        url = page.get("final_url") or page["url"]
        graph = graph_audit(data.get("jsonld", []), url) if quality["usable"] else None
        if graph is not None:
            for error in data.get("jsonld_errors", []):
                findings.append(
                    issue(
                        "jsonld_syntax",
                        url,
                        "warning",
                        error["error"],
                        "Fix invalid JSON syntax and recheck the rendered schema.",
                        subject=str(error["block"]),
                    )
                )
            if quality["absence_supported"] and not (
                graph["typed_entities"] or data.get("microdata_types") or data.get("jsonld_errors")
            ):
                findings.append(
                    issue(
                        "missing_schema",
                        url,
                        "warning",
                        "No typed structured data was observed.",
                        "Use applicable types and describe only facts visible on the page.",
                        effort=2,
                    )
                )
            for node in graph["findings"]:
                findings.append(
                    issue(
                        node["code"],
                        url,
                        "warning",
                        node["detail"],
                        "Compare the declaration and its references against visible content.",
                        effort=2,
                        subject=node["path"],
                    )
                )
        content.append(
            dict(
                url=url,
                captured_at=quality["captured_at"],
                representation=quality["representation"],
                limits=quality["limits"],
                citation_readiness=score(data, quality),
                graph=graph,
            )
        )
    return {
        "status": "complete"
        if any(row["graph"] is not None for row in content)
        else "insufficient_evidence",
        "findings": findings,
        "pages": content,
    }


def competitor_entity_gap_researcher(first_party, competitor_crawls):
    own = {kind for row in first_party for kind in (row["graph"] or {}).get("types", [])}
    comparisons = []
    for crawl in competitor_crawls:
        folder = Path(crawl)
        pages = read_pages(folder)
        evidence = {}
        for page in pages:
            if capture_quality(page)["absence_supported"]:
                data, _ = selected_data(page)
                url = page.get("final_url") or page["url"]
                for kind in graph_audit(data.get("jsonld", []), url)["types"]:
                    evidence.setdefault(kind, set()).add(url)
        comparisons.append(
            dict(
                target=read_json(folder / "summary.json")["seed"],
                inspected_pages=len(pages),
                review_candidates={
                    kind: sorted(evidence[kind]) for kind in sorted(evidence.keys() - own)
                },
            )
        )
    return {
        "status": "comparison_available" if comparisons else "needs_competitor_captures",
        "comparisons": comparisons,
        "review": "Review applicability and independently confirm direct competitor fit.",
    }


def backlink_digital_pr_strategist(reputation):
    if reputation is None:
        return {
            "status": "needs_verified_sources",
            "source_counts": {},
            "next": "Use discover and backlinks to verify relevant independent sources.",
        }
    if not isinstance(reputation.get("sources"), list):
        raise ValueError("Supply the reputation report containing assessed source rows.")
    sources = reputation["sources"]
    counts = Counter(
        row.get("classification") or row.get("relationship") or "unclassified" for row in sources
    )
    return {
        "status": "source_evidence_available",
        "source_counts": dict(counts),
        "estimated_reputation": reputation.get("score"),
        "next": "Prioritize editorial sources relevant to the audience. Develop a sourced story or reusable resource before outreach.",
    }
