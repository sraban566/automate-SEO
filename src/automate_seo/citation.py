"""Explainable content extractability signals for citation readiness."""

import re
from urllib.parse import urlsplit

QUANTITY = re.compile(
    r"(?<!\w)(?:\d+(?:[.,]\d+)?\s?(?:%|ms\b|km\b|kg\b|GB\b|USD\b)|(?:19|20)\d{2}\b)", re.I
)
METHOD = re.compile(
    r"\b(?:according to|we measured|sample size|methodology|dataset|study found)\b", re.I
)


def dom_signals(scope, url, has_main):
    """Collect bounded visible-content features from the crawler's chosen DOM scope."""
    paragraphs = scope.find_all("p", limit=120)
    answers = 0
    for head in scope.find_all(["h1", "h2", "h3"], limit=80):
        next_block = head.find_next(["p", "h1", "h2", "h3", "ul", "ol"])
        if next_block is not None and scope in next_block.parents and next_block.name == "p":
            answer = next_block.get_text(" ", strip=True)
            if 18 <= len(answer.split()) <= 80 and re.search(r"[.!?。]$", answer):
                answers += 1
    domains = set()
    for tag in scope.find_all("a", href=True, limit=250):
        try:
            destination = urlsplit(tag["href"])
        except ValueError:
            continue
        label = tag.get_text(" ", strip=True)
        if (
            destination.scheme in {"http", "https"}
            and destination.hostname
            and destination.hostname != urlsplit(url).hostname
            and len(label) >= 6
        ):
            domains.add(destination.hostname)
    return {
        "concise_heading_answers": answers,
        "semantic_landmark": has_main,
        "structured_blocks": len(scope.find_all(["dl", "table", "ol", "figure"], limit=20)),
        "factual_markers": sum(
            bool(
                QUANTITY.search(p.get_text(" ", strip=True))
                or METHOD.search(p.get_text(" ", strip=True))
            )
            for p in paragraphs
        ),
        "paragraphs_sampled": len(paragraphs),
        "external_source_domains": len(domains),
        "citation_elements": len(scope.find_all(["cite", "time"], limit=20)),
    }


def score(data, quality):
    signals = data.get("citation_signals")
    if not quality["absence_supported"] or signals is None:
        return {"score": None, "status": "insufficient_capture", "components": {}}
    density = signals["factual_markers"] / max(signals["paragraphs_sampled"], 1)
    components = {
        "concise_answer_blocks": min(30, 10 * signals["concise_heading_answers"]),
        "semantic_html": 15 * bool(signals["semantic_landmark"])
        + min(10, 5 * signals["structured_blocks"]),
        "factual_density": min(20, round(50 * density)),
        "citation_hooks": min(
            25, 8 * signals["external_source_domains"] + 4 * signals["citation_elements"]
        ),
    }
    return {
        "score": sum(components.values()),
        "status": "heuristic",
        "components": components,
        "signals": signals,
    }
