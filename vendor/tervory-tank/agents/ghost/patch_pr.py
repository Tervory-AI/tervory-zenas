"""Autumbra dep-bump draft PR opener. Flag OFF by default. Never merges. Never deploys.

Product: Autumbra (Watch/Enforce). Internal room path: agents/ghost.
Env: GHOST_PATCH_PR_ENABLED / PATCH_PR_ENABLED must be 1 to call GitHub; otherwise dry-run.
"""
from __future__ import annotations

import base64
import json
import os
import re
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from .patch_brief import PatchBrief, PatchEcosystem

ENABLED_ENV = "GHOST_PATCH_PR_ENABLED"
ENABLED_ALIASES = (ENABLED_ENV, "PATCH_PR_ENABLED")
TOKEN_ENVS = ("GHOST_GITHUB_TOKEN", "GH_TOKEN")
GITHUB_API = "https://api.github.com"
NEVER_MERGE_NOTE = "CI must pass; Autumbra never merges. A human approval tap is required for every code fix."


def enabled() -> bool:
    for name in ENABLED_ALIASES:
        if os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def _token(github_token_env: str | None = None) -> str | None:
    names = (github_token_env,) if github_token_env else TOKEN_ENVS
    for name in names:
        if not name:
            continue
        value = os.environ.get(name, "").strip()
        if value:
            return value
    # Fallback aliases when a specific env was requested but empty.
    if github_token_env:
        for name in TOKEN_ENVS:
            value = os.environ.get(name, "").strip()
            if value:
                return value
    return None


def _sanitize_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return cleaned[:80] or "pkg"


def plan_bump(brief: PatchBrief) -> dict[str, Any]:
    """Build branch/title/body for a draft dep-bump PR. No network."""
    eco = brief.ecosystem.value
    pkg = _sanitize_segment(brief.package)
    ver = _sanitize_segment(brief.to_version)
    branch = f"ghost/dep-bump-{eco}-{pkg}-{ver}"
    ids = [*brief.cve_ids, *brief.advisory_ids]
    id_line = ", ".join(ids) if ids else "(none listed)"
    title = f"Autumbra: bump {brief.package} {brief.from_version} → {brief.to_version} ({eco})"
    body = "\n".join(
        [
            "## Autumbra dependency bump (draft)",
            "",
            f"- **Package:** `{brief.package}` ({eco})",
            f"- **From:** `{brief.from_version}` → **To:** `{brief.to_version}`",
            f"- **Repo:** `{brief.repo}`",
            f"- **Site:** `{brief.site}`",
            f"- **Severity:** `{brief.severity.value}` (confidence {brief.confidence})",
            f"- **Advisories / CVEs:** {id_line}",
            f"- **Reason:** {brief.reason or '(none)'}",
            "",
            f"**{NEVER_MERGE_NOTE}**",
            "",
            "Flag `GHOST_PATCH_PR_ENABLED` must be on for Autumbra to open this PR; merge/deploy remain human-gated.",
        ]
    )
    return {
        "branch": branch,
        "title": title,
        "body": body,
        "base": "main",
        "draft": True,
        "repo": brief.repo,
        "package": brief.package,
        "ecosystem": eco,
        "from_version": brief.from_version,
        "to_version": brief.to_version,
    }


class GitHubClient:
    """Minimal GitHub REST helper (urllib). Never calls merge."""

    def __init__(self, token: str, http_get=None, http_send=None) -> None:
        self.token = token
        self._get = http_get or self._default_get
        self._send = http_send or self._default_send

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "autumbra-patch-pr",
            "Content-Type": "application/json",
        }

    def _default_get(self, url: str) -> tuple[int, dict | list | str | None]:
        req = urlrequest.Request(url, headers=self._headers(), method="GET")
        try:
            with urlrequest.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return resp.status, None
                try:
                    return resp.status, json.loads(raw)
                except json.JSONDecodeError:
                    return resp.status, raw
        except urlerror.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                return exc.code, json.loads(body) if body else None
            except json.JSONDecodeError:
                return exc.code, body

    def _default_send(self, method: str, url: str, payload: dict | None = None) -> tuple[int, dict | list | None]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urlrequest.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urlrequest.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, json.loads(raw) if raw else None
        except urlerror.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                return exc.code, json.loads(body) if body else None
            except json.JSONDecodeError:
                return exc.code, {"message": body}

    def get_repo(self, owner: str, repo: str) -> dict:
        status, data = self._get(f"{GITHUB_API}/repos/{owner}/{repo}")
        if status != 200 or not isinstance(data, dict):
            raise RuntimeError("GitHub repo lookup failed.")
        return data

    def get_ref_sha(self, owner: str, repo: str, ref: str) -> str:
        status, data = self._get(f"{GITHUB_API}/repos/{owner}/{repo}/git/ref/heads/{ref}")
        if status != 200 or not isinstance(data, dict):
            raise RuntimeError("GitHub ref lookup failed.")
        return data["object"]["sha"]

    def create_ref(self, owner: str, repo: str, branch: str, sha: str) -> None:
        status, data = self._send(
            "POST",
            f"{GITHUB_API}/repos/{owner}/{repo}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": sha},
        )
        if status not in {201, 422}:  # 422 = already exists; caller may reuse
            raise RuntimeError(f"GitHub create ref failed ({status}).")
        if status == 422 and isinstance(data, dict) and "already exists" not in str(data.get("message", "")).lower():
            # If the branch exists we still proceed to update via commit on that tip below.
            pass

    def get_file(self, owner: str, repo: str, path: str, ref: str) -> tuple[str | None, str | None]:
        status, data = self._get(f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}?ref={ref}")
        if status != 200 or not isinstance(data, dict):
            return None, None
        content_b64 = data.get("content")
        sha = data.get("sha")
        if not isinstance(content_b64, str):
            return None, sha if isinstance(sha, str) else None
        text = base64.b64decode(content_b64.replace("\n", "")).decode("utf-8", errors="replace")
        return text, sha if isinstance(sha, str) else None

    def create_blob(self, owner: str, repo: str, content: str) -> str:
        status, data = self._send(
            "POST",
            f"{GITHUB_API}/repos/{owner}/{repo}/git/blobs",
            {"content": content, "encoding": "utf-8"},
        )
        if status != 201 or not isinstance(data, dict) or "sha" not in data:
            raise RuntimeError("GitHub blob create failed.")
        return data["sha"]

    def create_tree(self, owner: str, repo: str, base_tree: str, path: str, blob_sha: str) -> str:
        status, data = self._send(
            "POST",
            f"{GITHUB_API}/repos/{owner}/{repo}/git/trees",
            {
                "base_tree": base_tree,
                "tree": [{"path": path, "mode": "100644", "type": "blob", "sha": blob_sha}],
            },
        )
        if status != 201 or not isinstance(data, dict) or "sha" not in data:
            raise RuntimeError("GitHub tree create failed.")
        return data["sha"]

    def create_commit(self, owner: str, repo: str, message: str, tree: str, parents: list[str]) -> str:
        status, data = self._send(
            "POST",
            f"{GITHUB_API}/repos/{owner}/{repo}/git/commits",
            {"message": message, "tree": tree, "parents": parents},
        )
        if status != 201 or not isinstance(data, dict) or "sha" not in data:
            raise RuntimeError("GitHub commit create failed.")
        return data["sha"]

    def update_ref(self, owner: str, repo: str, branch: str, sha: str) -> None:
        status, _ = self._send(
            "PATCH",
            f"{GITHUB_API}/repos/{owner}/{repo}/git/refs/heads/{branch}",
            {"sha": sha, "force": False},
        )
        if status != 200:
            raise RuntimeError("GitHub ref update failed.")

    def create_pull(self, owner: str, repo: str, title: str, body: str, head: str, base: str) -> dict:
        status, data = self._send(
            "POST",
            f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
            {"title": title, "body": body, "head": head, "base": base, "draft": True},
        )
        if status not in {201, 200} or not isinstance(data, dict):
            raise RuntimeError(f"GitHub draft PR create failed ({status}).")
        return data

    # Intentionally NO merge method. Autumbra never merges.


def _bump_pip_line(text: str, package: str, from_version: str, to_version: str) -> str | None:
    pattern = re.compile(
        rf"(?m)^(\s*{re.escape(package)}\s*==\s*){re.escape(from_version)}(\s*(?:#.*)?)?$"
    )
    new_text, n = pattern.subn(rf"\g<1>{to_version}\2", text, count=1)
    return new_text if n else None


def _bump_npm_dep(text: str, package: str, to_version: str) -> str | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    changed = False
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        block = data.get(section)
        if isinstance(block, dict) and package in block:
            block[package] = to_version if str(to_version).startswith(("^", "~", "=", ">")) else to_version
            changed = True
    if not changed:
        return None
    return json.dumps(data, indent=2) + "\n"


def _select_file_change(client: GitHubClient, brief: PatchBrief, base_ref: str) -> tuple[str, str]:
    """Return (path, new_content). Prefer real pin edit; else pending-bumps note."""
    owner, repo = brief.repo.split("/", 1)
    if brief.ecosystem is PatchEcosystem.pip:
        for path in ("requirements.txt", "requirements-prod.txt", "requirements/prod.txt"):
            text, _ = client.get_file(owner, repo, path, base_ref)
            if text is None:
                continue
            bumped = _bump_pip_line(text, brief.package, brief.from_version, brief.to_version)
            if bumped is not None:
                return path, bumped
    elif brief.ecosystem is PatchEcosystem.npm:
        text, _ = client.get_file(owner, repo, "package.json", base_ref)
        if text is not None:
            bumped = _bump_npm_dep(text, brief.package, brief.to_version)
            if bumped is not None:
                return "package.json", bumped

    path = f"docs/ghost/pending-bumps/{_sanitize_segment(brief.package)}.md"
    content = "\n".join(
        [
            f"# Pending bump: {brief.package}",
            "",
            f"- Ecosystem: `{brief.ecosystem.value}`",
            f"- From: `{brief.from_version}` → To: `{brief.to_version}`",
            f"- Site: `{brief.site}`",
            f"- Severity: `{brief.severity.value}`",
            f"- CVEs: {', '.join(brief.cve_ids) or '(none)'}",
            f"- Advisories: {', '.join(brief.advisory_ids) or '(none)'}",
            "",
            "Pin not found in lockfile/requirements on default branch.",
            "This note keeps the draft PR honest so CI still has a change to review.",
            "",
            NEVER_MERGE_NOTE,
            "",
        ]
    )
    return path, content


def open_dep_bump_pr(
    brief: PatchBrief,
    *,
    github_token_env: str | None = None,
    client: GitHubClient | None = None,
) -> dict[str, Any]:
    """Open a draft dep-bump PR, or dry-run when flag/token missing. Never merges."""
    plan = plan_bump(brief)
    if not enabled():
        return {
            "mode": "dry_run",
            "would_open": plan,
            "note": "Flag OFF. No PR opened.",
            "flag": ENABLED_ENV,
            "draft": True,
        }

    token = _token(github_token_env)
    if not token and client is None:
        return {
            "mode": "dry_run",
            "would_open": plan,
            "note": "Flag ON but GitHub token missing. No PR opened.",
            "flag": ENABLED_ENV,
            "draft": True,
        }

    gh = client or GitHubClient(token or "")
    owner, repo = brief.repo.split("/", 1)
    meta = gh.get_repo(owner, repo)
    default_branch = meta.get("default_branch") or "main"
    plan["base"] = default_branch
    base_sha = gh.get_ref_sha(owner, repo, default_branch)
    gh.create_ref(owner, repo, plan["branch"], base_sha)

    path, content = _select_file_change(gh, brief, default_branch)
    blob_sha = gh.create_blob(owner, repo, content)
    # base_tree is the commit tree of the default tip
    status, commit_obj = gh._get(f"{GITHUB_API}/repos/{owner}/{repo}/git/commits/{base_sha}")
    if status != 200 or not isinstance(commit_obj, dict):
        raise RuntimeError("GitHub commit lookup failed.")
    base_tree = commit_obj["tree"]["sha"]
    tree_sha = gh.create_tree(owner, repo, base_tree, path, blob_sha)
    commit_sha = gh.create_commit(
        owner,
        repo,
        f"chore(deps): bump {brief.package} to {brief.to_version} (Autumbra draft)",
        tree_sha,
        [base_sha],
    )
    gh.update_ref(owner, repo, plan["branch"], commit_sha)
    pr = gh.create_pull(owner, repo, plan["title"], plan["body"], plan["branch"], default_branch)
    return {
        "mode": "opened",
        "pr_url": pr.get("html_url"),
        "pr_number": pr.get("number"),
        "draft": True,
        "branch": plan["branch"],
        "path": path,
        "note": NEVER_MERGE_NOTE,
        "flag": ENABLED_ENV,
    }
