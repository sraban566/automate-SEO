"""Local tool contracts shared by the CLI and JSON-RPC clients."""

import json
import math
import sys
from pathlib import Path

from .entity_graph import graph_audit, web_url
from .evidence import capture_quality, selected_data
from .git_patch import git_patch
from .health import run_audit
from .review import read_pages


def audit(crawl_dir):
    return run_audit(crawl=crawl_dir)


def schema_fix(repo, file, changes, framework="html"):
    return git_patch(repo, file, changes, framework)


def content_brief(crawl_dir, url, question=None):
    pages = read_pages(Path(crawl_dir))
    page = next((p for p in pages if url in (p["url"], p.get("final_url"))), None)
    if page is None or not capture_quality(page)["absence_supported"]:
        raise ValueError("The brief needs a complete capture of the requested URL.")
    data, representation = selected_data(page)
    return {
        "target": url,
        "representation": representation,
        "captured_at": capture_quality(page)["captured_at"],
        "customer_question": question,
        "existing_title": data.get("title"),
        "existing_headings": data.get("headings", {}),
        "evidence_excerpt": data.get("main_text", "")[:800],
        "outline": [
            "Answer the customer's question directly in the opening paragraph.",
            "Explain qualifications, comparisons or steps under clear headings.",
            "Link factual claims to primary evidence and indicate scope and dates.",
            "Keep any JSON-LD consistent with facts visible in the final draft.",
        ],
        "schema_review": graph_audit(data.get("jsonld", []), url),
    }


TOOLS = {
    "audit": (audit, ("crawl_dir",), (), "Audit saved crawl evidence."),
    "schema-fix": (
        schema_fix,
        ("repo", "file", "changes"),
        ("framework",),
        "Draft a verified Git diff from reviewed changes.",
    ),
    "content-brief": (
        content_brief,
        ("crawl_dir", "url"),
        ("question",),
        "Prepare a content brief from a captured page.",
    ),
}


def call_tool(method, params):
    if method == "tools.list":
        if params:
            raise TypeError("tools.list takes no parameters.")
        return [
            {
                "name": name,
                "required": list(required),
                "optional": list(optional),
                "description": description,
            }
            for name, (_, required, optional, description) in TOOLS.items()
        ]
    fn, required, optional, _ = TOOLS[method]
    if set(params).difference((*required, *optional)) or set(required).difference(params):
        raise TypeError(
            f"Required parameters: {', '.join(required)}. Optional: {', '.join(optional)}."
        )
    for key, value in params.items():
        if key == "changes":
            if not isinstance(value, dict):
                raise TypeError("changes must be an object.")
        elif not isinstance(value, str) or not value.strip():
            raise TypeError(f"{key} must be a nonempty string.")
    if "url" in params and not web_url(params["url"]):
        raise ValueError("Provide an absolute HTTP(S) URL.")
    return fn(**params)


def handle_request(line):
    response = {"jsonrpc": "2.0", "id": None}

    def error(code, message):
        return {**response, "error": {"code": code, "message": message}}

    try:

        def reject_constant(value):
            raise ValueError(f"Invalid JSON constant: {value}")

        request = json.loads(line, parse_constant=reject_constant)
    except (ValueError, RecursionError):
        return error(-32700, "Parse error")
    if (
        not isinstance(request, dict)
        or request.get("jsonrpc") != "2.0"
        or not isinstance(request.get("method"), str)
        or type(request.get("id")) not in (str, int, float, type(None))
        or (isinstance(request.get("id"), float) and not math.isfinite(request["id"]))
    ):
        return error(-32600, "Invalid request")
    response["id"] = request.get("id")
    if "id" not in request:
        return None
    method = request["method"]
    params = request.get("params", {})
    if not isinstance(params, dict):
        return error(-32602, "Use named tool parameters.")
    if method not in (*TOOLS, "tools.list"):
        return error(-32601, "Method not found")
    try:
        return {**response, "result": call_tool(method, params)}
    except TypeError as exc:
        return error(-32602, str(exc))
    except (OSError, ValueError, KeyError, RecursionError) as exc:
        return error(-32000, str(exc))


def serve():
    for line in sys.stdin.buffer:
        response = handle_request(line)
        if response is not None:
            print(json.dumps(response, ensure_ascii=True, allow_nan=False), flush=True)
