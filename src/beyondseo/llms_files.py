"""Generate a curated llms.txt index and a bounded Markdown companion."""

import re
from pathlib import Path
from urllib.parse import quote, urljoin

from .entity_graph import web_url
from .evidence import capture_quality, combined_index_signals, selected_data
from .network import origin

LINK = re.compile(r"^\s*[-*+] \[(?:\\.|[^\]\\])+\]\((<?[^ )]+>?)\)(?:: .*)?$")
FULL_LINK = re.compile(r"^Source: <([^<>]+)>$", re.M)


def safe_label(value):
    return re.sub(r"([\\\[\]*_<>])", r"\\\1", " ".join(value.split()))


def validate_document(value, url, full=False):
    lines = value.lstrip("\ufeff").splitlines()
    errors = []
    warnings = []
    if not lines or not re.fullmatch(r"# \S.*", lines[0]):
        errors.append("Start with an H1 naming the site or project.")
    if "\0" in value:
        errors.append("Remove null characters.")
    urls = []
    if full:
        urls = FULL_LINK.findall(value)
        if not urls:
            warnings.append("No explicit source lines in this companion document.")
    else:
        section = False
        for number, line in enumerate(lines[1:], 2):
            if line.startswith("## "):
                section = True
            elif re.match(r"^#{1,6}(?:\s|$)", line):
                errors.append(f"Line {number}: use H2 file-list sections.")
            elif section and line.strip():
                link = LINK.fullmatch(line)
                if link:
                    urls.append(link.group(1).strip("<>"))
                else:
                    errors.append(f"Line {number}: expected a Markdown file-list link.")
    checked = []
    for link in urls:
        try:
            target = urljoin(url, link)
        except ValueError:
            target = ""
        if web_url(target):
            checked.append(target)
        else:
            errors.append(f"Invalid HTTP(S) link: {link}")
    if len(checked) != len(set(checked)):
        warnings.append("The same URL occurs more than once.")
    return dict(valid=not errors, errors=errors, warnings=warnings, links=checked)


def eligible_documents(pages, url):
    seen = set()
    for page in pages:
        target = page.get("final_url") or page.get("url")
        if not web_url(target) or origin(target) != origin(url) or target in seen:
            continue
        if not capture_quality(page)["absence_supported"]:
            continue
        signals = combined_index_signals(page)
        if not signals["indexability_candidate"] or signals["canonical_elsewhere"]:
            continue
        data, _ = selected_data(page)
        if data.get("markdown", "").strip() and data.get("word_count", 0):
            seen.add(target)
            yield target, data


def generate(pages, url, out, title=None, max_pages=20, max_chars=120_000):
    if not 1 <= max_pages <= 100 or not 1000 <= max_chars <= 1_000_000:
        raise ValueError("Choose 1–100 pages and 1,000–1,000,000 companion characters.")
    seed = next((page for page in pages if page["url"] == url), {})
    url = seed.get("final_url") or url
    documents = list(eligible_documents(pages, url))
    if not documents:
        raise ValueError("No complete, indexable same-origin captures are available.")
    documents.sort(key=lambda item: (item[0].rstrip("/") != url.rstrip("/"), item[0]))
    title = safe_label(title or documents[0][1].get("title") or origin(url))
    index = [f"# {title}", "", "> Curated pages from the public website.", "", "## Pages"]
    sections = [f"# {title}", "", "> Markdown extracted from public page captures."]
    included = []
    for target, data in documents[:max_pages]:
        label = safe_label(data.get("title") or target)
        href = quote(target, safe=":/?#[]@!$&'*+,;=%-._~")
        index.append(f"- [{label}]({href})")
        body = data["markdown"].strip()
        section = "\n".join([f"## {label}", "", f"Source: <{href}>", "", body])
        if sum(len(s) + 2 for s in sections) + len(section) <= max_chars:
            sections.append(section)
            included.append(target)
    index.extend(
        ["", "## Optional", f"- [Full Markdown companion]({origin(url)}/llms-full.txt)", ""]
    )
    if not included:
        raise ValueError("No captured page fits the companion size limit.")
    payloads = {"llms.txt": "\n".join(index), "llms-full.txt": "\n\n".join(sections) + "\n"}
    result = {}
    for name, value in payloads.items():
        status = validate_document(value, f"{origin(url)}/{name}", full=name != "llms.txt")
        if not status["valid"]:
            raise ValueError(f"Could not generate {name}: {status['errors']}")
        result[name] = status
    destination = Path(out)
    destination.mkdir(parents=True, exist_ok=True)
    if any((destination / name).exists() for name in payloads):
        raise ValueError("Choose a fresh output directory to preserve existing llms files.")
    for name, value in payloads.items():
        (destination / name).write_text(value, encoding="utf-8", newline="\n")
    return {
        "files": result,
        "index_pages": min(len(documents), max_pages),
        "full_pages": len(included),
        "full_omitted_pages": len(documents) - len(included),
    }
