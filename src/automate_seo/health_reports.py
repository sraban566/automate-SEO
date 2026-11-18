"""Self-contained executive reports from the health audit's common data."""

from collections import Counter
from html import escape
from pathlib import Path

from .engine import write_json

METHOD = (
    "Readiness scores reflect extractability signals in the captured content. "
    "AI citations and share of voice require separate observations of answers."
)
STAGES = {
    "technical_auditor": "Technical audit",
    "content_schema_architect": "Content and schema",
    "competitor_entity_gap_researcher": "Competitor entity comparison",
    "backlink_digital_pr_strategist": "Backlink and digital PR",
}


def markdown_text(value):
    value = escape(" ".join(str(value).split()), quote=False)
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def export_report(report, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "health.json", report)
    totals = Counter(row["severity"] for row in report["findings"])
    coverage = report["coverage"]
    read = report["citation_readiness_average"]
    metrics = [
        ("Pages attempted", coverage["attempted"]),
        ("Interpretable pages", coverage["interpretable"]),
        ("Pages scored", coverage["readiness_scored"]),
        ("Critical findings", totals["critical"]),
        ("Warnings", totals["warning"]),
        ("Citation readiness", f"{read}/100" if read is not None else "Unavailable"),
    ]
    stages = [
        (name, report["stages"][key]["status"].replace("_", " ").capitalize())
        for key, name in STAGES.items()
    ]
    gate = report["gate"]
    status = (
        "Report only" if gate["threshold"] == "none" else "Failed" if gate["failed"] else "Passed"
    )
    lines = [
        "# SEO and AI-search health",
        "",
        markdown_text(report["target"]),
        "",
        f"Generated — {report['generated_at']}",
        "",
        f"Gate — {status}",
        "",
        *(f"- {name} — {value}" for name, value in metrics),
        "",
        METHOD,
        "",
        "## Coverage by stage",
        "",
        *(f"- {name} — {value}" for name, value in stages),
        "",
        "## Prioritized actions",
        "",
    ]
    cells = []
    for f in report["findings"]:
        lines.extend(
            [
                f"### {f['code']} — {f['severity']}",
                "",
                f"- Page — {markdown_text(f['url'])}",
                f"- Evidence — {markdown_text(f['detail'])}",
                f"- Action — {markdown_text(f['action'])}",
                f"- Impact {f['impact']} / effort {f['effort']} · priority {f['priority']}",
                "",
            ]
        )
        values = [
            f["severity"],
            f["url"],
            f["detail"],
            f["action"],
            f"{f['impact']} / {f['effort']} · {f['priority']}",
        ]
        cells.append("<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in values) + "</tr>")
    if not cells:
        lines.append("No findings for the captured scope.")
    (out / "health.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    cards = "".join(
        f"<div><dt>{escape(name)}</dt><dd>{escape(str(value))}</dd></div>"
        for name, value in metrics
    )
    rows = "".join(cells) or '<tr><td colspan="5">No findings for the captured scope.</td></tr>'
    stage_rows = "".join(f"<li>{escape(name)} — {escape(value)}</li>" for name, value in stages)
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>SEO and AI-search health · automate-SEO</title>
<style>
*{{box-sizing:border-box}} body{{margin:0;color:#172238;background:#f3f6fb;font:16px system-ui}}
main{{max-width:1250px;margin:auto;padding:clamp(18px,4vw,56px)}} header{{padding:2rem 0}}
h1{{font-size:clamp(1.8rem,4vw,3rem);margin:0}} p,td{{overflow-wrap:anywhere}}
dl{{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:12px}}
dl div{{background:#fff;border:1px solid #d4deec;border-radius:10px;padding:1rem}}
dt{{font-weight:600}} dd{{font-size:1.55rem;margin:.35rem 0 0;color:#194994}}
.table{{overflow:auto;border:1px solid #d4deec;border-radius:10px}}
table{{width:100%;border-collapse:collapse;background:#fff;text-align:left}}
th,td{{padding:12px;vertical-align:top;border-bottom:1px solid #e2e8f0}}
th{{background:#172d4d;color:#fff}} tr:last-child td{{border-bottom:0}}
</style></head><body><main><header><h1>SEO and AI-search health</h1>
<p>{escape(report["target"])}</p><p>Gate — {status} · {escape(report["generated_at"])}</p></header>
<dl>{cards}</dl><p>{METHOD}</p><h2>Coverage by stage</h2><ul>{stage_rows}</ul>
<h2>Prioritized actions</h2>
<div class="table"><table><thead><tr><th>Severity</th><th>Page</th><th>Evidence</th>
<th>Action</th><th>Impact / effort · priority</th></tr></thead><tbody>{rows}</tbody></table></div>
</main></body></html>"""
    (out / "health.html").write_text(page, encoding="utf-8")
