"""Local fixtures for AI-search health checks and review-only agent tools."""

import json
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from automate_seo.agent_tools import handle_request
from automate_seo.cli import main
from automate_seo.entity_graph import graph_audit
from automate_seo.extract import extract
from automate_seo.git_patch import git_patch
from automate_seo.health import evaluate_gate, research_sources, run_audit
from automate_seo.llms_files import generate, validate_document
from automate_seo.review import read_pages

HOST = "https://example.org/"
ORG = {
    "@context": "https://schema.org",
    "@id": "#org",
    "@type": "Organization",
    "name": "Fixture",
    "url": HOST,
    "sameAs": "https://example.net/fixture",
}


class Site(BaseHTTPRequestHandler):
    def do_GET(self):
        routes = {
            "/": (
                200,
                "text/html",
                f"""<!doctype html><html><head><title>Fixture</title>
              <meta name="description" content="Example research">
              <link rel="canonical" href="/"><script type="application/ld+json">{json.dumps(ORG)}</script>
              </head><body><main><h1>What did the study measure?</h1>
              <p>In 2026 we measured 35% better throughput under fixed conditions with repeated
              trials and publicly documented methods from the original research team.</p>
              <ol><li>Gather evidence.</li></ol><p><cite>Fixture study</cite>.
              <a href="https://example.net/research">Primary study results</a></p>
              <a href="/product">Product</a></main></body></html>""",
            ),
            "/product": (
                200,
                "text/html",
                """<html><head><title>Product</title>
              <link rel="canonical" href="/gone"></head><body>
              <main><h1>Fixture product</h1><p>Product information.</p></main></body></html>""",
            ),
            "/robots.txt": (
                200,
                "text/plain",
                "User-agent: OAI-SearchBot\nDisallow: /product\nUser-agent: GPTBot\nDisallow: /\n",
            ),
            "/llms.txt": (200, "text/plain", "# Fixture\n\n## Main\n- [Home](/)\n"),
            "/llms-full.txt": (200, "text/plain", "<html>soft failure</html>"),
        }
        status, content_type, body = routes.get(self.path, (404, "text/plain", "Missing."))
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


class AutomationTests(unittest.TestCase):
    def test_zero_scorable_pages_fail_even_if_present_in_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crawl = root / "crawl"
            crawl.mkdir()
            paths = {
                "summary.json": {"seed": HOST},
                "robots.json": {HOST.rstrip("/"): {"status": 404}},
                "pages.jsonl": {
                    "url": HOST,
                    "status": 200,
                    "data": extract(
                        '<html><body><script src="/app.js"></script></body></html>', HOST
                    ),
                },
            }
            for file, data in paths.items():
                (crawl / file).write_text(json.dumps(data) + "\n", encoding="utf-8")

            first = root / "first"
            command = ["health", "--crawl", str(crawl), "--fail-on", "critical"]
            self.assertEqual(main([*command, "--out", str(first)]), 1)
            baseline = first / "health.json"
            report = json.loads(baseline.read_text(encoding="utf-8"))
            self.assertEqual(report["coverage"]["readiness_scored"], 0)
            finding = next(row for row in report["findings"] if row["code"] == "no_scorable_pages")
            self.assertEqual(finding["severity"], "critical")
            self.assertIn(finding["detail"], (first / "health.md").read_text(encoding="utf-8"))
            self.assertIn(finding["detail"], (first / "health.html").read_text(encoding="utf-8"))
            self.assertEqual(
                main([*command, "--out", str(root / "second"), "--baseline", str(baseline)]), 1
            )

    def test_research_passes_discovery_evidence_to_bounded_source_verification(self):
        lead = {"url": "https://example.net/story", "verification": "unverified_lead"}
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("automate_seo.discovery.discover") as discover,
            patch("automate_seo.reputation.reputation") as reputation,
        ):
            discover.return_value = {
                "status": "leads_available",
                "search_available": True,
                "native_requests": 3,
                "candidate_count": 1,
                "candidates": [lead],
            }
            evidence, verified = research_sources(
                [{"query": "Fixture report"}], HOST, "Fixture", tmp
            )
            self.assertEqual(evidence["leads"], [lead])
            self.assertIs(verified, reputation.return_value)
            self.assertEqual(reputation.call_args.args[0], Path(tmp) / "discovery/sources.csv")
            self.assertEqual(reputation.call_args.kwargs["limit"], 6)

    def test_nested_graph_resolves_cross_block_references_and_reports_invalid_shapes(self):
        graph = graph_audit(
            [
                ORG,
                {
                    "@graph": [
                        {"@type": "FAQPage", "mainEntity": {"@id": "#q"}},
                        {
                            "@id": "#q",
                            "@type": "Question",
                            "name": "What?",
                            "acceptedAnswer": {"@id": "#a"},
                        },
                        {"@id": "#a", "@type": "Answer", "text": "An answer."},
                        {
                            "@type": "HowTo",
                            "name": "Do it",
                            "step": {
                                "@type": "HowToSection",
                                "itemListElement": [{"@type": "HowToStep", "text": "One step."}],
                            },
                        },
                        {
                            "@type": "Article",
                            "headline": "Fixture article",
                            "datePublished": "2026-10-01",
                            "author": {"@id": "#org"},
                        },
                        {
                            "@type": "Product",
                            "name": "Fixture",
                            "offers": {"@type": "Offer", "price": "3", "priceCurrency": "USD"},
                        },
                    ]
                },
            ],
            HOST,
        )
        self.assertTrue(graph["valid"], graph["findings"])
        self.assertEqual(graph["findings"], [])
        invalid = graph_audit(
            [
                {
                    "@graph": [
                        {"@type": "FAQPage", "mainEntity": {"@id": f"{HOST}#missing"}},
                        {"@id": "http://[invalid", "@type": [], "sameAs": "javascript:alert(1)"},
                        {"@id": "#org", "name": "Different"},
                    ]
                },
                ORG,
            ],
            HOST,
        )
        self.assertFalse(invalid["valid"])
        self.assertTrue(
            {
                "unresolved_local_id",
                "invalid_id",
                "invalid_type",
                "invalid_same_as",
                "conflicting_identity",
            }
            <= {item["code"] for item in invalid["findings"]}
        )

    def test_live_gate_offline_replay_guidance_and_rpc_use_the_same_capture(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            url = f"http://127.0.0.1:{server.server_port}/"
            status = main(
                [
                    "health",
                    "--url",
                    url,
                    "--out",
                    tmp,
                    "--max-pages",
                    "2",
                    "--allow-private",
                    "--fail-on",
                    "critical",
                ]
            )
            self.assertEqual(status, 1)
            report = json.loads((root / "health.json").read_text(encoding="utf-8"))
            codes = {f["code"] for f in report["findings"]}
            self.assertTrue(
                {
                    "search_bot_blocked",
                    "broken_canonical",
                    "missing_meta_description",
                    "missing_schema",
                    "llms_invalid",
                }
                <= codes,
                codes,
            )
            self.assertTrue(report["gate"]["failed"])
            self.assertFalse(evaluate_gate(report, "critical", report)["failed"])
            self.assertGreater(report["citation_readiness_average"], 0)
            self.assertIsNone(report["measured_ai_share_of_voice"])
            self.assertIn("Prioritized actions", (root / "health.html").read_text(encoding="utf-8"))
            self.assertIn("broken_canonical", (root / "health.md").read_text(encoding="utf-8"))

            crawl = root / "crawl"
            replay = run_audit(crawl=crawl)
            self.assertEqual(replay["coverage"], report["coverage"])
            drafted = generate(read_pages(crawl), url, root / "guidance")
            self.assertEqual(drafted["full_pages"], 1)
            self.assertTrue(drafted["files"]["llms.txt"]["valid"])
            self.assertIn(url, drafted["files"]["llms-full.txt"]["links"])

            result = handle_request(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "content-brief",
                        "params": {"crawl_dir": str(crawl), "url": url},
                    }
                )
            )
            self.assertEqual(result["result"]["existing_title"], "Fixture")
            self.assertEqual(handle_request("{")["error"]["code"], -32700)

    def test_llms_validator_handles_single_heading_relative_links_and_bad_sections(self):
        self.assertTrue(validate_document("\ufeff# A", HOST)["valid"])
        self.assertEqual(
            validate_document("# A\n## Posts\n- [Post](post.md)", HOST)["links"], [HOST + "post.md"]
        )
        self.assertFalse(validate_document("# A\n## Posts\nbad entry", HOST)["valid"])

    def test_git_patches_validate_for_all_targets_without_modifying_tracked_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def git(*args, **kwargs):
                return subprocess.run(
                    ["git", *args], cwd=root, check=True, capture_output=True, **kwargs
                )

            git("init", "-q")
            files = {
                "index.html": (
                    "html",
                    "<!doctype html><html><head></head><body>Body</body></html>",
                ),
                "page.tsx": (
                    "next",
                    "// automate-seo:metadata\nexport default function Page() {\n"
                    "  return <main>\n    {/* automate-seo:schema */}\n  </main>;\n}\n",
                ),
                "App.jsx": (
                    "react",
                    "export default function App() {\n  return <>\n"
                    "    {/* automate-seo:metadata */}\n    {/* automate-seo:schema */}\n  </>;\n}\n",
                ),
            }
            for filename, (_, body) in files.items():
                (root / filename).write_bytes(body.encode())
            git("add", ".")
            git(
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.org",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "fixture",
            )
            for filename, (framework, body) in files.items():
                with self.subTest(framework=framework):
                    result = git_patch(
                        root,
                        filename,
                        {
                            "title": "Updated",
                            "description": "Study",
                            "canonical": HOST,
                            "schema": ORG,
                        },
                        framework,
                    )
                    self.assertEqual(result["status"], "drafted")
                    self.assertIn("application/ld+json", result["diff"])
                    git("apply", "--check", "-", input=result["diff"].encode())
                    self.assertEqual((root / filename).read_bytes(), body.encode())
            (root / "App.jsx").write_bytes(b"dirty")
            with self.assertRaisesRegex(ValueError, "outstanding"):
                git_patch(root, "App.jsx", {"title": "Updated"}, "react")
