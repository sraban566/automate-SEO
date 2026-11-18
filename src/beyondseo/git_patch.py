"""Draft apply-checked Git patches for explicitly reviewed metadata and JSON-LD."""

import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from .entity_graph import graph_audit
from .onsite import draft_html, short_text


def run_git(root, *args, input_bytes=None):
    return subprocess.run(
        ["git", *args], cwd=root, input=input_bytes, capture_output=True, check=False
    )


def patch_framework(before, changes, framework):
    newline = "\r\n" if "\r\n" in before else "\n"

    def replace(source, marker, replacement):
        matches = [
            m for m in re.finditer(r"(?m)^([ \t]*)" + re.escape(marker) + r"[ \t]*(?=\r?$)", source)
        ]
        if len(matches) != 1:
            raise ValueError(f"Place exactly one {marker} marker where this patch should go.")
        m = matches[0]
        insertion = newline.join(m[1] + part for part in replacement.splitlines())
        return source[: m.start()] + insertion + source[m.end() :]

    if {"title", "description", "canonical"} & changes.keys():
        if framework == "next":
            if re.search(
                r"\bexport\s+(?:[^\r\n]*\bmetadata\b|(?:async\s+)?function\s+generateMetadata\b)",
                before,
            ):
                raise ValueError(
                    "Review and merge the existing Next.js metadata with the framework editor."
                )
            if re.match(r"\s*['\"]use client['\"]", before):
                raise ValueError("Next.js metadata requires a server component.")
            data = {key: changes[key] for key in ("title", "description") if key in changes}
            if "canonical" in changes:
                data["alternates"] = {"canonical": changes["canonical"]}
            before = replace(
                before,
                "// automate-seo:metadata",
                "export const metadata = " + json.dumps(data, indent=2) + ";",
            )
        else:
            if re.search(
                r"""<title\b|<meta\b[^>]*\bname\s*=\s*['"]description['"]|<link\b[^>]*\brel\s*=\s*['"]canonical['"]""",
                before,
                re.I,
            ):
                raise ValueError(
                    "Review and merge existing React metadata with the framework editor."
                )
            lines = []
            if "title" in changes:
                lines.append("<title>{" + json.dumps(changes["title"]) + "}</title>")
            if "description" in changes:
                lines.append(
                    '<meta name="description" content={'
                    + json.dumps(changes["description"])
                    + "} />"
                )
            if "canonical" in changes:
                lines.append(
                    '<link rel="canonical" href={' + json.dumps(changes["canonical"]) + "} />"
                )
            before = replace(before, "{/* automate-seo:metadata */}", "\n".join(lines))
    if "schema" in changes:
        if "application/ld+json" in before:
            raise ValueError("Review and merge the existing JSON-LD with the framework editor.")
        payload = json.dumps(changes["schema"], allow_nan=False).replace("<", "\\u003c")
        script = (
            '<script type="application/ld+json" dangerouslySetInnerHTML={{ __html: '
            + json.dumps(payload)
            + " }} />"
        )
        before = replace(before, "{/* automate-seo:schema */}", script)
    return before


def git_patch(root, filename, changes, framework):
    root = Path(root).resolve(strict=True)
    prefix = run_git(root, "rev-parse", "--show-prefix")
    if prefix.returncode or prefix.stdout.strip():
        raise ValueError("Provide the root of a Git worktree.")
    rel = PurePosixPath(filename)
    if (
        rel.is_absolute()
        or ".." in rel.parts
        or ".git" in rel.parts
        or not re.fullmatch(r"[A-Za-z0-9_./()@\[\]+-]+", filename)
    ):
        raise ValueError("Use a tracked path relative to the repository root.")
    source = root.joinpath(*rel.parts)
    if source.resolve(strict=True) != source or not source.is_file():
        raise ValueError("Use a regular file without symlink components.")
    expected = {
        "html": {".html", ".htm"},
        "next": {".jsx", ".tsx", ".js", ".ts"},
        "react": {".jsx", ".tsx", ".js", ".ts"},
    }
    if framework not in expected or source.suffix.lower() not in expected[framework]:
        raise ValueError("Choose the framework matching the source extension.")
    if run_git(
        root, "--literal-pathspecs", "ls-files", "--error-unmatch", "--", rel.as_posix()
    ).returncode:
        raise ValueError("The source file must already be tracked in Git.")
    status = run_git(root, "--literal-pathspecs", "status", "--porcelain", "--", rel.as_posix())
    if status.returncode or status.stdout:
        raise ValueError("Commit or separate outstanding changes to this source before drafting.")
    if (
        not isinstance(changes, dict)
        or not changes
        or changes.keys() - {"title", "description", "canonical", "schema"}
    ):
        raise ValueError("Supply reviewed title, description, canonical or schema fields.")
    for key, maximum in (("title", 200), ("description", 500)):
        if key in changes:
            changes = {**changes, key: short_text(changes[key], key, maximum)}
    if "canonical" in changes:
        from .entity_graph import web_url

        if not web_url(changes["canonical"]) or "#" in changes["canonical"]:
            raise ValueError("Canonical must be an absolute HTTP(S) URL without a fragment.")
    graph = None
    if "schema" in changes:
        schema = changes["schema"]
        if not isinstance(schema, (list, dict)) or not schema:
            raise ValueError("Supply a populated JSON-LD object or array.")
        graph = graph_audit([schema], changes.get("canonical", "https://example.invalid/"))
        if not graph["valid"] or not graph["typed_entities"]:
            raise ValueError(
                "Fix invalid declarations and include at least one typed entity before drafting."
            )
        json.dumps(schema, allow_nan=False)
    before = source.read_bytes()
    if len(before) > 2_000_000:
        raise ValueError("Choose a source file smaller than 2 MB.")
    if framework == "html":
        after, _ = draft_html(before, changes)
        if b"\r\n" in before:
            after = after.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    else:
        after = patch_framework(before.decode("utf-8"), changes, framework).encode("utf-8")
    digest = hashlib.sha256(before).hexdigest()
    if before == after:
        return {"status": "no_changes", "file": rel.as_posix(), "before_sha256": digest, "diff": ""}
    with tempfile.TemporaryDirectory() as tmp:
        draft = Path(tmp)
        (draft / "old").write_bytes(before)
        (draft / "new").write_bytes(after)
        result = run_git(
            draft, "diff", "--no-index", "--no-color", "--no-ext-diff", "--text", "--", "old", "new"
        )
    if result.returncode != 1 or not result.stdout.startswith(b"diff --git "):
        raise ValueError("Git could not produce a reviewable diff.")
    path = rel.as_posix().encode()
    diff = result.stdout.replace(
        b"diff --git a/old b/new\n", b"diff --git a/" + path + b" b/" + path + b"\n", 1
    )
    diff = diff.replace(b"--- a/old\n+++ b/new\n", b"--- a/" + path + b"\n+++ b/" + path + b"\n", 1)
    verified = run_git(root, "apply", "--check", "--whitespace=nowarn", "-", input_bytes=diff)
    if verified.returncode:
        raise ValueError(
            "Git could not apply the diff cleanly: " + verified.stderr.decode(errors="replace")
        )
    return {
        "status": "drafted",
        "file": rel.as_posix(),
        "before_sha256": digest,
        "graph_review": graph,
        "diff": diff.decode("utf-8"),
    }
