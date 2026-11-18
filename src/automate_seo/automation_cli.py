"""Register and dispatch the automation commands."""

import json
from pathlib import Path

from .agent_tools import call_tool, serve
from .health import evaluate_gate, run_audit
from .health_reports import export_report
from .llms_files import generate, validate_document
from .review import read_json, read_pages


def register(sub):
    health = sub.add_parser("health", help="Run and report SEO and AI-search health checks.")
    source = health.add_mutually_exclusive_group(required=True)
    source.add_argument("--url")
    source.add_argument("--crawl", type=Path)
    health.add_argument("--out", type=Path, required=True)
    health.add_argument("--max-pages", type=int, default=10)
    health.add_argument("--mode", choices=("http", "auto", "browser"), default="http")
    health.add_argument(
        "--timeout", type=float, default=10, help="Seconds per fetch or page render."
    )
    health.add_argument(
        "--browser-assets",
        choices=("public", "allowlist"),
        default="public",
        help="Load public dependencies or restrict browser requests to the crawl scope.",
    )
    health.add_argument("--allow-private", action="store_true")
    health.add_argument("--competitor-crawl", type=Path, action="append", default=[])
    health.add_argument("--reputation", type=Path)
    health.add_argument(
        "--research-queries", type=Path, help="Run bounded discovery from reviewed query JSON."
    )
    health.add_argument("--brand", help="Brand name for source verification when running research.")
    health.add_argument("--baseline", type=Path)
    health.add_argument("--fail-on", choices=("none", "warning", "critical"), default="critical")

    llms = sub.add_parser("llms", help="Generate or check AI guidance files.")
    actions = llms.add_subparsers(dest="operation", required=True)
    draft = actions.add_parser("generate")
    draft.add_argument("--crawl", type=Path, required=True)
    draft.add_argument("--out", type=Path, required=True)
    draft.add_argument("--title")
    draft.add_argument("--max-pages", type=int, default=20)
    draft.add_argument("--max-chars", type=int, default=120_000)
    validate = actions.add_parser("validate")
    validate.add_argument("file", type=Path)
    validate.add_argument("--url", required=True, help="Public URL for resolving relative links.")
    validate.add_argument("--full", action="store_true", help="Validate the Markdown companion.")

    agent = sub.add_parser("agent", help="Invoke a local specialist tool with JSON inputs.")
    agent.add_argument("method", choices=("audit", "schema-fix", "content-brief", "tools.list"))
    agent.add_argument("--input", type=Path, help="JSON file with named tool parameters.")
    sub.add_parser(
        "rpc", help="Serve local tools using newline-delimited JSON-RPC on stdin/stdout."
    )


def execute(args):
    if args.command == "rpc":
        serve()
        return 0
    if args.command == "health":
        report = run_audit(
            url=args.url,
            crawl=args.crawl,
            out=args.out,
            max_pages=args.max_pages,
            mode=args.mode,
            timeout=args.timeout,
            browser_assets=args.browser_assets,
            allow_private=args.allow_private,
            competitors=args.competitor_crawl,
            reputation=args.reputation,
            research_queries=args.research_queries,
            brand=args.brand,
        )
        baseline = read_json(args.baseline) if args.baseline else None
        report["gate"] = evaluate_gate(report, args.fail_on, baseline)
        export_report(report, args.out)
        result = {
            "out": str(args.out),
            "target": report["target"],
            "coverage": report["coverage"],
            "gate": report["gate"],
            "findings": len(report["findings"]),
        }
        status = int(report["gate"]["failed"])
    elif args.command == "agent":
        params = read_json(args.input) if args.input else {}
        if not isinstance(params, dict):
            raise TypeError("Agent parameters must be a JSON object.")
        result = call_tool(args.method, params)
        status = 0
    elif args.operation == "generate":
        pages = read_pages(args.crawl)
        seed = read_json(args.crawl / "summary.json")["seed"]
        result = generate(pages, seed, args.out, args.title, args.max_pages, args.max_chars)
        status = 0
    else:
        from .entity_graph import web_url

        if not web_url(args.url):
            raise ValueError("Supply the public HTTP(S) file URL.")
        result = validate_document(
            args.file.read_text(encoding="utf-8-sig"),
            args.url,
            args.full or args.file.name == "llms-full.txt",
        )
        status = int(not result["valid"])
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return status
