"""Offline JSON-LD entity checks for the Schema.org vocabulary used in SEO."""

from collections import defaultdict
from urllib.parse import urldefrag, urljoin, urlsplit


def values(value):
    return value if isinstance(value, list) else [] if value is None else [value]


def web_url(value):
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        return False
    try:
        p = urlsplit(value)
        return p.scheme in {"http", "https"} and bool(p.hostname) and p.username is None
    except ValueError:
        return False


def types(node):
    names = [t for t in values(node.get("@type")) if isinstance(t, str)]
    for prefix in ("https://schema.org/", "http://schema.org/", "schema:"):
        names = [t.removeprefix(prefix) for t in names]
    return set(names)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def entity_id(value, page_url):
    if not text(value) or any(ch.isspace() for ch in value):
        return None
    try:
        return urljoin(page_url, value)
    except ValueError:
        return None


def graph_audit(blocks, page_url):
    """Resolve same-document IDs across blocks and inspect typed nested entities.

    Schema.org is open-world. Missing fields are readiness recommendations, and
    an external reference is allowed without fetching any remote JSON-LD context.
    """
    nodes = []
    indexed = defaultdict(list)
    findings = []
    stack = [(block, f"block[{i}]") for i, block in reversed(list(enumerate(blocks)))]

    def add(code, path, detail, severity="warning"):
        findings.append(dict(code=code, path=path, detail=detail, severity=severity))

    while stack:
        value, path = stack.pop()
        if isinstance(value, list):
            stack.extend((item, f"{path}[{i}]") for i, item in reversed(list(enumerate(value))))
        elif isinstance(value, dict):
            nodes.append((value, path))
            identifier = entity_id(value.get("@id"), page_url)
            if "@id" in value and not identifier:
                add("invalid_id", path, "@id must be a usable IRI or local reference.", "error")
            if identifier and value.keys() - {"@id", "@context"}:
                indexed[identifier].append(value)
            if "@graph" in value and (
                value["@graph"] is None
                or not all(isinstance(item, dict) for item in values(value["@graph"]))
            ):
                add("invalid_graph", path, "@graph must contain node objects.", "error")
            declared_types = value.get("@type")
            if "@type" in value and (
                not values(declared_types) or not all(text(t) for t in values(declared_types))
            ):
                add("invalid_type", path, "@type must contain nonempty strings.", "error")
            stack.extend(
                (child, f"{path}.{key}")
                for key, child in reversed(list(value.items()))
                if key != "@context" and isinstance(child, (dict, list))
            )
        elif path.startswith("block[") and "." not in path:
            add("invalid_root", path, "JSON-LD root entries must be objects.", "error")

    def fragments(node):
        if not isinstance(node, dict):
            return []
        identifier = entity_id(node.get("@id"), page_url)
        return [node, *(part for part in indexed.get(identifier, []) if part is not node)]

    def fields(node, key):
        return [v for part in fragments(node) for v in values(part.get(key))]

    def is_type(node, *wanted):
        return any(types(part).intersection(wanted) for part in fragments(node))

    def need(node, prop, path, predicate=text):
        items = fields(node, prop)
        if not items or not all(predicate(item) for item in items):
            add("entity_property", f"{path}.{prop}", f"Supply a usable {prop} backed by the page.")
            return []
        return items

    def step_ok(step, seen=frozenset()):
        if text(step):
            return True
        if not isinstance(step, dict) or id(step) in seen or len(seen) >= 32:
            return False
        if is_type(step, "HowToSection"):
            children = fields(step, "itemListElement")
            return bool(children) and all(step_ok(child, seen | {id(step)}) for child in children)
        return is_type(step, "HowToStep") and any(
            text(v) for v in fields(step, "text") + fields(step, "name")
        )

    for identifier, parts in indexed.items():
        for prop in ("name", "headline", "url"):
            unique = {v.strip() for p in parts for v in values(p.get(prop)) if text(v)}
            explicitly_multivalued = any(isinstance(p.get(prop), list) for p in parts)
            if len(unique) > 1 and not explicitly_multivalued:
                add("conflicting_identity", identifier, f"Conflicting {prop} declarations for @id.")

    checked_ids = set()
    observed_types = set()
    document = urldefrag(page_url)[0]
    for node, path in nodes:
        identifier = node.get("@id")
        absolute_id = entity_id(identifier, page_url)
        is_reference = not node.keys() - {"@id", "@context"}
        if (
            is_reference
            and absolute_id
            and urldefrag(absolute_id)[0] == document
            and urldefrag(absolute_id)[1]
            and absolute_id not in indexed
        ):
            add(
                "unresolved_local_id",
                path,
                f"Local reference {identifier} has no definition.",
                "error",
            )
        if is_reference or (absolute_id and absolute_id in checked_ids):
            continue
        if absolute_id:
            checked_ids.add(absolute_id)
        declared = {kind for part in fragments(node) for kind in types(part)}
        observed_types.update(declared)
        if any("sameAs" in part for part in fragments(node)):
            same_as = fields(node, "sameAs")
            if not same_as or not all(web_url(link) and link != absolute_id for link in same_as):
                add(
                    "invalid_same_as",
                    f"{path}.sameAs",
                    "Use absolute URLs identifying the same entity.",
                    "error",
                )
        if declared & {
            "Organization",
            "Corporation",
            "LocalBusiness",
            "NGO",
            "EducationalOrganization",
        }:
            need(node, "name", path)
            need(node, "url", path, web_url)
            if not absolute_id:
                add("unstable_entity_id", path, "Give the organization a stable @id.")
        if declared & {"Article", "BlogPosting", "NewsArticle", "TechArticle", "ScholarlyArticle"}:
            need(node, "headline", path)
            need(
                node,
                "author",
                path,
                lambda v: (
                    is_type(v, "Person", "Organization")
                    or (isinstance(v, dict) and web_url(v.get("@id")) and len(v) == 1)
                ),
            )
            need(node, "datePublished", path)
        if "Product" in declared:
            need(node, "name", path)
            if not any(fields(node, p) for p in ("offers", "review", "aggregateRating")):
                add("product_details", path, "Review applicable offers, reviews or rating markup.")
            for offer in fields(node, "offers"):
                if is_type(offer, "Offer") and fields(offer, "price"):
                    need(offer, "priceCurrency", f"{path}.offers")
        if "FAQPage" in declared:
            for question in need(node, "mainEntity", path, lambda v: is_type(v, "Question")):
                need(question, "name", f"{path}.mainEntity")
                for answer in need(
                    question, "acceptedAnswer", f"{path}.mainEntity", lambda v: is_type(v, "Answer")
                ):
                    need(answer, "text", f"{path}.mainEntity.acceptedAnswer")
        if "HowTo" in declared:
            need(node, "name", path)
            need(node, "step", path, step_ok)

    return {
        "typed_entities": sum(bool(types(node)) for node, _ in nodes),
        "declared_ids": sorted(indexed),
        "types": sorted(observed_types),
        "findings": findings,
        "valid": not any(f["severity"] == "error" for f in findings),
    }
