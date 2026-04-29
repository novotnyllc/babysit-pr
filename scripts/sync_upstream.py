#!/usr/bin/env python3
"""Apply upstream openai/codex babysit-pr skill changes onto this plugin."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
METADATA_PATH = REPO_ROOT / ".upstream" / "babysit-pr.json"
README_PATH = REPO_ROOT / "README.md"
NOTICE_PATH = REPO_ROOT / "NOTICE"


def run(cmd: list[str], cwd: Path = REPO_ROOT, *, capture: bool = False) -> str:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=capture,
    )
    return proc.stdout if capture else ""


def load_metadata() -> dict[str, str]:
    data = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    required = {
        "upstream_repo",
        "upstream_ref",
        "upstream_path",
        "local_path",
        "upstream_commit",
    }
    missing = sorted(required - data.keys())
    if missing:
        raise RuntimeError(f"Missing metadata fields: {', '.join(missing)}")
    return data


def transformed_patch(patch: str, upstream_path: str, local_path: str) -> str:
    upstream = upstream_path.strip("/")
    local = local_path.strip("/")
    out: list[str] = []
    path_line_prefixes = (
        "diff --git ",
        "--- ",
        "+++ ",
        "rename from ",
        "rename to ",
        "copy from ",
        "copy to ",
    )
    for line in patch.splitlines(keepends=True):
        if line.startswith(path_line_prefixes):
            line = line.replace(f"a/{upstream}/", f"a/{local}/")
            line = line.replace(f"b/{upstream}/", f"b/{local}/")
            line = line.replace(f"{upstream}/", f"{local}/")
        out.append(line)
    return "".join(out)


def replace_commit_references(old_commit: str, new_commit: str) -> None:
    for path in (README_PATH, NOTICE_PATH):
        text = path.read_text(encoding="utf-8")
        updated = text.replace(old_commit, new_commit)
        if updated == text:
            updated = re.sub(r"\b[0-9a-f]{40}\b", new_commit, text, count=1)
        path.write_text(updated, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check for upstream changes without modifying the working tree.",
    )
    args = parser.parse_args()

    metadata = load_metadata()
    old_commit = metadata["upstream_commit"]

    with tempfile.TemporaryDirectory(prefix="babysit-pr-upstream-") as tmp:
        upstream = Path(tmp) / "codex"
        run(["git", "init", str(upstream)])
        run(["git", "remote", "add", "origin", metadata["upstream_repo"]], cwd=upstream)
        run(
            ["git", "fetch", "--filter=blob:none", "--no-tags", "origin", metadata["upstream_ref"]],
            cwd=upstream,
        )
        new_commit = run(["git", "rev-parse", "FETCH_HEAD"], cwd=upstream, capture=True).strip()
        run(["git", "fetch", "--filter=blob:none", "--no-tags", "origin", old_commit], cwd=upstream)

        diff_cmd = [
            "git",
            "diff",
            "--binary",
            old_commit,
            new_commit,
            "--",
            metadata["upstream_path"],
        ]
        patch = run(diff_cmd, cwd=upstream, capture=True)
        if not patch:
            print(f"No upstream changes for {metadata['upstream_path']} at {new_commit}.")
            return 0

        if args.check:
            print(f"Upstream changes available: {old_commit}..{new_commit}")
            return 1

        mapped_patch = transformed_patch(
            patch,
            upstream_path=metadata["upstream_path"],
            local_path=metadata["local_path"],
        )
        patch_path = Path(tmp) / "upstream.patch"
        patch_path.write_text(mapped_patch, encoding="utf-8")
        run(["git", "apply", "--3way", str(patch_path)])

        metadata["upstream_commit"] = new_commit
        METADATA_PATH.write_text(json.dumps(metadata, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        replace_commit_references(old_commit, new_commit)
        print(f"Applied upstream changes: {old_commit}..{new_commit}")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"Command failed: {' '.join(exc.cmd)}", file=sys.stderr)
        raise
