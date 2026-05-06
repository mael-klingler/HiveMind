#!/usr/bin/env python3
"""
Orchestrator – K8s/PVC-ready Workspace Generator

Loads .env from /app/config/.env or local .env
and configures Git token auth automatically.
"""

import asyncio
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from database import set_ticket_ai_planning, get_enabled_mcp_servers, get_enabled_agent_instructions, get_agent_mcp_servers, get_agent_assigned_instructions, get_enabled_plugin_names, get_agent_memory_as_markdown, get_setting


def branch_name_for(ticket) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', (ticket.title or "").lower()).strip('-')[:40]
    return f"feature/{ticket.id}-{slug}" if slug else f"feature/{ticket.id}"


# ── .env Loader ────────────────────────────────────────────────────

def _load_dotenv(path: str = "/app/config/.env"):
    """Loads Key=Value pairs from a .env file into os.environ."""
    p = Path(path)
    if not p.exists():
        p = Path(".env")
        if not p.exists():
            return
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                key = k.strip()
                value = v.strip().strip('\'"')
                if key not in os.environ:
                    os.environ[key] = value

_load_dotenv()


# ── Environment Configuration (central, Defaults for local development) ──

ORCHESTRATOR_CONFIG = os.getenv("ORCHESTRATOR_CONFIG", "/app/config/orchestrator_config.json")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "glm-5.1:cloud")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))
OPENCODE_MODEL = os.getenv("OPENCODE_MODEL", "glm-5.1:cloud")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
AGENT_NAMESPACE = os.getenv("AGENT_NAMESPACE", "hivemind")
AGENT_IMAGE = os.getenv("AGENT_IMAGE", "hivemind-opencode:latest")
GITLAB_HOST = os.getenv("GITLAB_HOST") or ""
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))
OPENCODE_PORT = os.getenv("OPENCODE_PORT", "4096")
OLLAMA_CLOUD_API_KEY = os.getenv("OLLAMA_CLOUD_API_KEY", "")


# ── Data Classes ─────────────────────────────────────────────────

@dataclass
class RepoConfig:
    name: str
    url: str
    branch: str
    description: str
    tags: List[str]

    @classmethod
    def from_dict(cls, data: dict) -> "RepoConfig":
        return cls(
            name=data["name"],
            url=data["url"],
            branch=data.get("branch", "development"),
            description=data.get("description", ""),
            tags=data.get("tags", []),
        )


@dataclass
class OrchestratorConfig:
    work_dir: str
    pvc_mount_path: str
    track_branch: str
    branch_fallback_order: List[str]
    auto_pull_interval_minutes: int
    leankg_enabled: bool
    ollama_host: str
    ollama_model: str
    max_related_files_per_repo: int
    log_level: str
    repositories: List[RepoConfig]

    @classmethod
    def from_file(cls, path: str) -> "OrchestratorConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        from database import get_all_repos, import_repos_from_config, get_setting
        import_repos_from_config(path)

        db_repos = get_all_repos(active_only=True)
        repositories = [RepoConfig.from_dict(r) for r in db_repos]

        fallback_str = get_setting("branch_fallback_order") or data.get("branch_fallback_order", "development,qa,main")

        db_track_branch = get_setting("default_branch")

        return cls(
            work_dir=data.get("work_dir", "/app/workspace"),
            pvc_mount_path=data.get("pvc_mount_path", "/app/workspace/repos"),
            track_branch=db_track_branch or data.get("track_branch", "development"),
            branch_fallback_order=[b.strip() for b in fallback_str.split(",") if b.strip()],
            auto_pull_interval_minutes=data.get("auto_pull_interval_minutes", 60),
            leankg_enabled=data.get("leankg_enabled", True),
            ollama_host=os.getenv("OLLAMA_HOST", data.get("ollama_host", "http://localhost:11434")),
            ollama_model=os.getenv("OLLAMA_MODEL", data.get("ollama_model", "glm-5.1:cloud")),
            max_related_files_per_repo=data.get("max_related_files_per_repo", 5),
            log_level=data.get("log_level", "INFO"),
            repositories=repositories,
        )


@dataclass
class RepoStatus:
    config: RepoConfig
    local_path: Path
    exists: bool = False
    changes_detected: bool = False
    latest_commit: str = ""
    error: str = ""
    leankg_ready: bool = False


@dataclass
class Ticket:
    id: str
    title: str = ""
    description: str = ""
    labels: List[str] = field(default_factory=list)
    issue_type: str = "Task"
    priority: str = "Medium"
    agent_id: str = ""
    selected_repos: List[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, path: str) -> "Ticket":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            id=str(data.get("id", data.get("key", "UNKNOWN"))),
            title=data.get("title", data.get("summary", "")),
            description=data.get("description", ""),
            labels=data.get("labels", []),
            issue_type=data.get("issue_type", data.get("type", "Task")),
            priority=data.get("priority", "Medium"),
            selected_repos=data.get("selected_repos", []),
        )


# ── Logger ─────────────────────────────────────────────────────────

import logging as _logging

_struct_log = _logging.getLogger("hivemind.main")

class Logger:
    def info(self, msg: str):
        _struct_log.info(msg)

    def ok(self, msg: str):
        _struct_log.info(msg)

    def warn(self, msg: str):
        _struct_log.warning(msg)

    def error(self, msg: str):
        _struct_log.error(msg)

    def step(self, msg: str):
        _struct_log.info(msg)

    def sub(self, msg: str):
        _struct_log.info(msg)

log = Logger()


# ── Git Manager ────────────────────────────────────────────────────

class RepoManager:
    def __init__(self, base_dir: str, default_branch: str, fallback_order: List[str] = None):
        self.base_dir = Path(base_dir)
        self.default_branch = default_branch
        self.fallback_order = fallback_order or [default_branch]

    def _run(self, *cmd, cwd: Optional[str] = None) -> Tuple[int, str, str]:
        try:
            result = subprocess.run(list(cmd), capture_output=True, text=True, cwd=cwd, timeout=120)
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return -101, "", f"Git command timed out: {' '.join(cmd)}"
        except FileNotFoundError:
            return -100, "", "git not found"

    async def _arun(self, *cmd, cwd: Optional[str] = None) -> Tuple[int, str, str]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._run, *cmd, cwd)

    def ensure_repo(self, config: RepoConfig) -> RepoStatus:
        repo_dir = self.base_dir / config.name
        status = RepoStatus(config=config, local_path=repo_dir)

        branch = config.branch or self.default_branch

        if repo_dir.exists() and (repo_dir / ".git").exists():
            rc, _, _ = self._run("git", "fetch", "origin", str(branch), cwd=str(repo_dir))
            if rc != 0:
                status.error = f"fetch failed: {branch}"
                log.error(f"{config.name}: fetch failed")
                log.error(f"Stderr: {_}")
                return status

            rc, local_commit, _ = self._run("git", "rev-parse", f"heads/{branch}", cwd=str(repo_dir))
            rc2, remote_commit, _ = self._run("git", "rev-parse", f"origin/{branch}", cwd=str(repo_dir))
            status.latest_commit = remote_commit or local_commit

            if local_commit != remote_commit:
                status.changes_detected = True
                log.sub(f"{config.name}: new commits ({local_commit[:8]} → {remote_commit[:8]})")
                rc, _, err = self._run("git", "reset", "--hard", f"origin/{branch}", cwd=str(repo_dir))
                if rc:
                    status.error = f"pull failed: {err}"
            else:
                log.sub(f"{config.name}: up-to-date ({remote_commit[:8]})")

            status.exists = True
        else:
            url = config.url
            token = os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))
            if re.match(r"^https?://", url) and token and "@" not in url.split("://")[1].split("/")[0]:
                git_user = get_setting("git_user") or os.getenv("GIT_USER", "gitlab-ci-token")
                url = re.sub(r"^(https?://)", rf"\1{git_user}:{token}@", url)
            rc, out, err = self._run("git", "clone", "--depth=100", "-b", str(branch), url, str(repo_dir))
            if rc != 0 and "not found in upstream" in err:
                for fallback_branch in self.fallback_order:
                    if fallback_branch == str(branch):
                        continue
                    log.sub(f"{config.name}: branch '{branch}' not found, trying '{fallback_branch}'...")
                    rc, out, err = self._run("git", "clone", "--depth=100", "-b", fallback_branch, url, str(repo_dir))
                    if rc == 0:
                        break
            if rc != 0:
                status.error = f"Clone failed: {err}"
                log.error(f"{config.name}: clone failed")
                log.error(f"Stderr: {err}")
                return status
            rc, commit, _ = self._run("git", "rev-parse", "HEAD", cwd=str(repo_dir))
            status.latest_commit = commit
            status.exists = True
            log.sub(f"{config.name}: cloned ({commit[:8]})")

        return status

    async def aensure_repo(self, config: RepoConfig) -> RepoStatus:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.ensure_repo, config)

    def init_all(self, repos: List[RepoConfig]) -> List[RepoStatus]:
        log.step("Repository Initialization")
        return [self.ensure_repo(r) for r in repos]

    def update_all(self, repos: List[RepoConfig]) -> List[RepoStatus]:
        log.step("Repository Update")
        return [self.ensure_repo(r) for r in repos]

    async def ainit_all(self, repos: List[RepoConfig]) -> List[RepoStatus]:
        log.step("Repository Initialization (async)")
        loop = asyncio.get_event_loop()
        tasks = [loop.run_in_executor(None, self.ensure_repo, r) for r in repos]
        return await asyncio.gather(*tasks)

    async def aupdate_all(self, repos: List[RepoConfig]) -> List[RepoStatus]:
        log.step("Repository Update (async)")
        loop = asyncio.get_event_loop()
        tasks = [loop.run_in_executor(None, self.ensure_repo, r) for r in repos]
        return await asyncio.gather(*tasks)


# ── LeanKG Manager ────────────────────────────────────────────────

class LeanKGManager:
    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self.cmd = "leankg"

    def _run(self, *args, cwd: Optional[str] = None) -> Tuple[int, str, str]:
        try:
            result = subprocess.run([self.cmd, *args], capture_output=True, text=True, cwd=cwd, timeout=60)
            return result.returncode, result.stdout, result.stderr
        except FileNotFoundError:
            return -100, "", "leankg not found"
        except subprocess.TimeoutExpired:
            return -101, "", "Timeout"

    def is_available(self) -> bool:
        rc, _, _ = self._run("--version")
        return rc == 0

    def init_repo(self, path: Path) -> bool:
        if (path / ".leankg").exists():
            return True
        rc, _, _ = self._run("init", cwd=str(path))
        return rc == 0

    def index_repo(self, path: Path) -> bool:
        self.init_repo(path)
        rc, _, _ = self._run("index", ".", cwd=str(path))
        return rc == 0

    def check_repo_context(self, path: Path, query: str, limit: int = 5) -> List[Dict]:
        rc, out, _ = self._run("query", *query, cwd=str(path))
        if rc != 0:
            return []
        results = []
        for line in out.strip().splitlines()[:limit]:
            if line.strip():
                results.append({"summary": line.strip()})
        return results

    def index_all(self, statuses: List[RepoStatus]):
        if not self.is_available():
            log.warn("LeanKG CLI not available → skipping indexing (repos are still cloned)")
            return
        log.step("LeanKG Indexing")
        for s in statuses:
            if s.error:
                continue
            log.sub(f"{s.config.name}: indexing...")
            if self.index_repo(s.local_path):
                log.ok(f"{s.config.name}: indexed")
                s.leankg_ready = True


# ── Ollama Client ────────────────────────────────────────────────

class OllamaClient:
    MAX_RETRIES = 3
    RETRY_DELAYS = [5, 15, 30]

    def __init__(self, host: str = OLLAMA_HOST, model: str = OLLAMA_MODEL, timeout: int = OLLAMA_TIMEOUT):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout

    def _post(self, payload: dict) -> dict:
        import httpx
        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                body = json.dumps(payload).encode("utf-8")
                headers = {"Content-Type": "application/json"}
                api_key = os.getenv("OLLAMA_CLOUD_API_KEY", "")
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                base_url = os.getenv("OLLAMA_BASE_URL", self.host + "/v1")
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(f"{base_url.rstrip('/')}/chat/completions", content=body, headers=headers)
                    resp.raise_for_status()
                    return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (503, 429, 502) and attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt]
                    _struct_log.warning(f"Ollama HTTP {e.response.status_code} (attempt {attempt+1}/{self.MAX_RETRIES}), retry in {delay}s")
                    import time
                    time.sleep(delay)
                    last_error = RuntimeError(f"Ollama HTTP {e.response.status_code}: {e.response.text}")
                    continue
                raise RuntimeError(f"Ollama HTTP {e.response.status_code}: {e.response.text}") from e
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt]
                    _struct_log.warning(f"Ollama not reachable (attempt {attempt+1}/{self.MAX_RETRIES}), retry in {delay}s")
                    import time
                    time.sleep(delay)
                    last_error = e
                    continue
                raise RuntimeError(f"Ollama not reachable after {self.MAX_RETRIES} attempts: {e}") from e
        raise last_error or RuntimeError("Ollama failed")

    def is_available(self) -> bool:
        import httpx
        try:
            base_url = os.getenv("OLLAMA_BASE_URL", self.host + "/v1")
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{base_url.rstrip('/')}/models")
                return resp.status_code == 200
        except Exception:
            return False

    def select_model_for_complexity(self, analysis: Dict) -> str:
        """Selects LLM model based on ticket complexity. Returns model name."""
        complexity = analysis.get("complexity", "Medium")
        routing_enabled = get_setting("model_routing_enabled") or os.getenv("MODEL_ROUTING_ENABLED", "false")
        if str(routing_enabled).lower() != "true":
            return self.model
        simple_model = get_setting("simple_model") or os.getenv("SIMPLE_MODEL", self.model)
        complex_model = get_setting("complex_model") or os.getenv("COMPLEX_MODEL", self.model)
        if complexity in ("Low",) and simple_model:
            log.info(f"Model routing: {complexity} complexity → {simple_model}")
            return simple_model
        elif complexity in ("High",) and complex_model:
            log.info(f"Model routing: {complexity} complexity → {complex_model}")
            return complex_model
        return self.model

    def analyze_repos_for_ticket(self, ticket: Ticket, repo_contexts: List[RepoStatus], leankg: LeanKGManager) -> Dict:
        prompt = self._make_prompt(ticket, repo_contexts, leankg)
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": (
                    "You are a Senior Architect. Analyze the ticket and select the repositories "
                    "most likely affected. Respond ONLY as JSON."
                )},
                {"role": "user", "content": prompt}
            ],
            "stream": False,
            "format": "json"
        }
        raw = self._post(body)
        content = raw.get("message", {}).get("content", "")
        if not content:
            choices = raw.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
        if not content:
            raise RuntimeError(f"Ollama returned no answer: {raw}")
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*\n?", "", content)
            content = re.sub(r"\n?\s*```\s*$", "", content)
            content = content.strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r'\{.*\}', content, re.DOTALL)
            if m:
                return json.loads(m.group(0))
            raise RuntimeError(f"No JSON in Ollama response: {content[:300]}")

    def _make_prompt(self, ticket: Ticket, repo_contexts: List[RepoStatus], leankg: LeanKGManager) -> str:
        blocks = []
        for ctx in repo_contexts:
            if ctx.error:
                continue
            keywords = (ticket.title + " " + ticket.description).lower().split()[:20]
            leankg_files = []
            if ctx.leankg_ready:
                leankg_files = [r["summary"] for r in leankg.check_repo_context(ctx.local_path, " ".join(keywords))]
            blocks.append({
                "name": ctx.config.name,
                "description": ctx.config.description or "No description",
                "tags": ctx.config.tags,
                "leankg_context": {"indexed": ctx.leankg_ready, "recent_files": leankg_files[:5]},
            })

        return json.dumps({
            "instruction": ("Select 1–4 repositories for this ticket. "
                            "Return JSON with: selected_repos[], primary_repo, "
                            "complexity (Low/Medium/High), estimated_hours, reasoning."),
            "ticket": {
                "id": ticket.id,
                "title": ticket.title,
                "description": ticket.description,
                "labels": ticket.labels,
            },
            "repositories": blocks,
        }, ensure_ascii=False, indent=2)




# ── Helper: Git Credentials ──────────────────────────────────────

def configure_git_credentials():
    from database import get_all_repos, get_setting as _gs
    token = os.getenv("GITLAB_TOKEN") or os.getenv("GIT_TOKEN") or ""
    git_user = _gs("git_user") or os.getenv("GIT_USER", "gitlab-ci-token")
    hosts = set()
    default_host = os.getenv("GITLAB_HOST") or ""
    if default_host:
        hosts.add(default_host)
    try:
        repos = get_all_repos()
        for r in repos:
            url = r.get("url", "") if isinstance(r, dict) else getattr(r, "url", "")
            if url and "://" in url:
                host = url.split("://")[1].split("/")[0].split(":")[0]
                hosts.add(host)
    except Exception:
        pass
    git_dir = Path.home() / ".git-credentials"
    if token and hosts:
        lines = [f"https://{git_user}:{token}@{h}\n" for h in sorted(hosts)]
        git_dir.write_text("".join(lines), encoding="utf-8")
        subprocess.run(["git", "config", "--global", "credential.helper", "store"], check=False)
        subprocess.run(["git", "config", "--global", "user.email", "hivemind-agents@example.com"], check=False)
        subprocess.run(["git", "config", "--global", "user.name", "HiveMind"], check=False)
        log.ok(f"Git credentials set for {', '.join(sorted(hosts))}")
    else:
        log.warn("GITLAB_TOKEN not set – possible clone errors")


# ── Workspace Utilities ─────────────────────────────────────────

def create_opencode_config(workspace_dir: Path, ticket: Ticket, selected: List[RepoConfig],
                           analysis: Dict, assignment_md: str):
    """Creates .opencode/opencode.json for the agent."""

    git_user = get_setting("git_user") or os.getenv("GIT_USER", "gitlab-ci-token")
    git_token = os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))
    repo_list = []
    for r in selected:
        remote = r.url or f"https://{os.getenv('GITLAB_HOST') or ''}/{r.name}.git"
        repo_list.append({
            "url": remote,
            "name": r.name,
            "branch": r.branch or "development",
            "primary": r.name == analysis.get("primary_repo", ""),
        })

    config = {
        "ticket": {
            "id": ticket.id,
            "title": ticket.title,
            "description": ticket.description,
            "labels": ticket.labels,
            "issue_type": ticket.issue_type,
            "priority": ticket.priority,
            "branch": branch_name_for(ticket),
        },
        "analysis": analysis,
        "repositories": repo_list,
        "assignment": assignment_md,
    }

    opencode_dir = workspace_dir / ".opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)

    (opencode_dir / "config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")

    log.ok("Workspace .opencode/ created")


def create_launch_scripts(workspace_dir: Path):
    """Creates start.sh and entrypoint.sh in the workspace."""
    start_sh = workspace_dir / "start.sh"
    start_sh.write_text("""#!/bin/bash
set -e
echo "🚀 Starting agent..."
cd "$(dirname "$0")"
if [ -f .opencode/opencode.json ]; then
    echo "📋 Task loaded:"
    cat .opencode/config.json | jq -r '.ticket.title'
fi
bash "$(dirname "$0")/entrypoint.sh"
""", encoding="utf-8")

    entrypoint_sh = workspace_dir / "entrypoint.sh"
    entrypoint_sh.write_text("""#!/bin/bash
set -e
WORKSPACE="$(pwd)"
BRANCH=$(jq -r '.ticket.branch // "feature/" + .ticket.id' $WORKSPACE/.opencode/config.json)
echo "📋 Ticket:  $(jq -r '.ticket.id' $WORKSPACE/.opencode/config.json) – $(jq -r '.ticket.title' $WORKSPACE/.opencode/config.json)"
echo "🌿 Branch:  $BRANCH"
echo "🧪 Dry-Run: false"
echo "🤖 Starting opencode with task..."
export OPENCODE_MODEL="ollama/{OPENCODE_MODEL}"
export OLLAMA_HOST="https://ollama.com/v1"
export OLLAMA_CLOUD_API_KEY="{OLLAMA_CLOUD_API_KEY}"
cd "$WORKSPACE"
for repo in $(jq -r '.repositories[].name' $WORKSPACE/.opencode/config.json); do
    if [ -d "$repo" ]; then
        echo "📦 $repo: Processing..."
        cd "$WORKSPACE/$repo"
        if ! git diff --quiet HEAD 2>/dev/null; then
            echo "✅ Changes detected, creating commit..."
            git add -A
            git commit -m "Agent: $(jq -r '.ticket.title' $WORKSPACE/.opencode/config.json)"
            git push origin HEAD:"$BRANCH" || true
        else
            echo "📦 $repo: No changes, skipping."
        fi
        cd "$WORKSPACE"
    fi
done
echo "🏁 All repos processed."
""", encoding="utf-8")

    start_sh.chmod(0o755)
    entrypoint_sh.chmod(0o755)
    log.ok("Launch scripts created")


def generate_assignment_prompt(ticket: Ticket, analysis: Dict, repos: List[RepoConfig]) -> str:
    complexity = analysis.get("complexity", "Medium")
    estimates = analysis.get("estimated_hours", "?")
    primary = analysis.get("primary_repo", "–")
    reasoning = analysis.get("reasoning", "")

    retry_context = ""
    review_notes = analysis.get("review_notes", "")
    mr_url = analysis.get("mr_url", "")
    pipeline_status = analysis.get("pipeline_status", "")
    retry_count = analysis.get("retry_count", 0)

    if retry_count > 0 or review_notes or pipeline_status == "failed":
        retry_context = f"""

## ⚠️ Retry Context (Attempt {retry_count + 1})
This ticket has already been processed, but there were issues that need to be fixed:

IMPORTANT: Push your changes to the same existing branch `{branch_name_for(ticket)}`. Do NOT create a new branch. The branch already exists on the remote.

"""
        if pipeline_status == "failed":
            retry_context += "- **Pipeline failed** – Please ensure all tests and typechecks pass.\n"
        if review_notes:
            retry_context += f"- **Review feedback:** {review_notes}\n"
        if mr_url:
            retry_context += f"- **MR link:** {mr_url}\n"
        conflict_status = analysis.get("conflict_status", "")
        if conflict_status == "conflict_detected":
            retry_context += "- **Merge conflict** – The branch has conflicts with the target branch. Resolve the conflicts, rebase onto the target branch, and push with force push.\n"

    repo_summaries = "\n".join(
        f"  • **{r.name}** – {r.description or 'No description'} (Tags: {', '.join(r.tags)})"
        for r in repos
    )

    return f"""# 🎯 Task: {ticket.id} – {ticket.title}

## Priority: {ticket.priority} | Type: {ticket.issue_type} | Complexity: {complexity} (~{estimates}h)

## Primary Repository: `{primary}`
{retry_context}
## Description
{ticket.description}

## Selected Repositories ({len(repos)})
{repo_summaries}

## Assessment
{reasoning}

## Tasks
1. Make code changes in the repositories listed above.
2. Add unit/integration tests.
3. Commit with descriptive message (Conventional Commits).
4. Push branch `{branch_name_for(ticket)}` (force push if branch already exists).
5. Create or update merge request (title = ticket title, description = change summary).

## Acceptance Criteria
- [ ] Ticket requirement fully implemented.
- [ ] Tests cover the changes.
- [ ] Clean commits in all affected repos.
- [ ] No regressions.
- [ ] Typecheck and lint pass (if CI is present).

## Notes
- Follow existing coding conventions.
- If unclear: analyze architecture and interfaces.
- Consider the tech stack.
"""

    instructions_raw = ""
    _agent_id = getattr(ticket, 'agent_id', None) or analysis.get("agent_id")
    if _agent_id:
        agent_instrs = get_agent_assigned_instructions(_agent_id)
        if agent_instrs:
            instructions_raw = "\n\n".join(i["content"] for i in agent_instrs)
    if not instructions_raw:
        instructions_raw = get_enabled_agent_instructions()
    if instructions_raw:
        prompt += f"""

## Agent Instructions
{instructions_raw}
"""

    return prompt


# ── Agent Pod Spawner ────────────────────────────────────────────

def _kubectl(args: str) -> Tuple[int, str, str]:
    from k8s_client import kubectl_compat
    return kubectl_compat(args)


def _ensure_ollama_secret():
    from k8s_client import create_namespaced_secret, get_secret, AGENT_NAMESPACE as K8S_NS
    if not OLLAMA_CLOUD_API_KEY:
        return False
    ns = os.getenv("AGENT_NAMESPACE", "hivemind")
    existing = get_secret("ollama-cloud-api-key", ns)
    if existing:
        return True
    try:
        create_namespaced_secret("ollama-cloud-api-key", {"api-key": OLLAMA_CLOUD_API_KEY}, ns)
        log.ok("Ollama Cloud Secret created")
        return True
    except Exception as e:
        raise RuntimeError(f"Could not create Ollama Cloud Secret: {e}")


def _sanitize_yaml_value(val: str) -> str:
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', str(val))


def spawn_agent_pod(ticket: Ticket, selected: List[RepoConfig], assignment_md: str, analysis: Dict):
    git_user = get_setting("git_user") or os.getenv("GIT_USER", "gitlab-ci-token")
    gitlab_token = get_setting("git_token") or GITLAB_TOKEN
    gitlab_host = get_setting("git_host") or os.getenv("GITLAB_HOST") or ""

    mcp_servers = []
    agent_id = ticket.agent_id if hasattr(ticket, 'agent_id') and ticket.agent_id else None
    if agent_id:
        agent_mcps = get_agent_mcp_servers(agent_id)
        mcp_servers = agent_mcps if agent_mcps else get_enabled_mcp_servers()
    else:
        mcp_servers = get_enabled_mcp_servers()

    memory_md = ""
    if agent_id:
        try:
            memory_md = get_agent_memory_as_markdown(agent_id, "")
        except Exception:
            memory_md = ""

    from pod_builder import spawn_agent_pod as _spawn_agent_pod
    return _spawn_agent_pod(
        ticket_id=ticket.id,
        ticket_title=ticket.title,
        repos=[{"name": r.name, "url": r.url, "branch": r.branch} for r in selected],
        assignment_md=assignment_md,
        analysis=analysis,
        agent_id=agent_id or "",
        mcp_servers=mcp_servers,
        plugin_names=get_enabled_plugin_names(),
        memory_md=memory_md,
        ollama_base_url=OLLAMA_BASE_URL,
        opencode_model=OPENCODE_MODEL,
        ollama_cloud_api_key=OLLAMA_CLOUD_API_KEY,
        gitlab_host=gitlab_host,
        git_user=git_user,
        gitlab_token=gitlab_token,
    )


# ── Orchestrator ───────────────────────────────────────────────────

class Orchestrator:
    def __init__(self, config_path: str):
        self.config = OrchestratorConfig.from_file(config_path)
        Path(self.config.pvc_mount_path).mkdir(parents=True, exist_ok=True)
        self.git = RepoManager(self.config.pvc_mount_path, self.config.track_branch, self.config.branch_fallback_order)
        self.leankg = LeanKGManager(self.config)
        self.llm = OllamaClient(self.config.ollama_host, self.config.ollama_model)
        self._statuses: List[RepoStatus] = []

    def init(self):
        log.step("=== Orchestrator Initialization ===")
        log.info(f"Work-Dir:  {self.config.work_dir}")
        log.info(f"PVC path:  {self.config.pvc_mount_path}")
        log.info(f"Branch:    {self.config.track_branch}")
        log.info(f"Repos:     {len(self.config.repositories)}")

        configure_git_credentials()

        self._statuses = self.git.init_all(self.config.repositories)

        if self.config.leankg_enabled:
            self.leankg.index_all(self._statuses)

        log.step("Initialization complete")

    def update(self):
        log.step("=== Repository Update ===")
        self._statuses = self.git.update_all(self.config.repositories)

        changed = [s for s in self._statuses if s.changes_detected]
        if changed:
            log.info(f"{len(changed)} repos had changes → Re-index via LeanKG")
            if self.config.leankg_enabled:
                for s in changed:
                    if not s.error and self.leankg.index_repo(s.local_path):
                        s.leankg_ready = True
                        log.ok(f"{s.config.name}: re-indexed")
        else:
            log.info("No changes detected.")
        return self._statuses

    def process(self, ticket: Ticket, use_llm: bool, skip_clone: bool) -> Path:
        self.update()

        log.step("Selecting required repositories")
        analysis = None

        if use_llm and self.llm.is_available():
            log.info("Using Ollama...")
            try:
                analysis = self.llm.analyze_repos_for_ticket(ticket, self._statuses, self.leankg)
                log.info(f"LLM selection:    {analysis.get('selected_repos', [])}")
                log.info(f"Primary:           {analysis.get('primary_repo')}")
                log.info(f"Complexity:        {analysis.get('complexity')}")
            except RuntimeError as e:
                log.error(f"LLM error: {e}")
                analysis = None
        elif use_llm:
            log.error(f"Ollama not reachable ({self.llm.host})")

        if not analysis:
            log.error("No AI analysis available – aborting")
            return None

        selected_names = set(analysis.get("selected_repos", []))
        selected_configs = [r for r in self.config.repositories if r.name in selected_names]

        log.step("Generating prompt")
        prompt = generate_assignment_prompt(ticket, analysis, selected_configs)

        set_ticket_ai_planning(ticket.id, analysis)

        log.step("Building workspace")
        workspace_dir = Path(self.config.work_dir) / f"workspace_{ticket.id}"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        create_opencode_config(workspace_dir / ".opencode", ticket, selected_configs, analysis, prompt)
        create_launch_scripts(workspace_dir)

        log.step("Starting agent pod")
        spawn_agent_pod(ticket, selected_configs, prompt, analysis)

        return workspace_dir


# ── CLI ───────────────────────────────────────────────────────────

def print_usage():
    print("""Usage: python main.py <command> [...]

Commands:
  init                         Pull all repos + index via LeanKG
  init-repos                   Import GitLab projects + AI tags/description
  update                       Update all repos + delta + re-index
  process <ticket.json> [...]  Analyze ticket + start agent pod in cluster
  serve                        Start HTTP server for ticket API

(process Flags: --llm, --llm-only, --no-clone, --leankg-only)

Environment (from .env or direct):
  ORCHESTRATOR_CONFIG=/app/config/orchestrator_config.json
  AGENT_NAMESPACE=hivemind
  AGENT_IMAGE=hivemind-opencode:v2
  GITLAB_TOKEN=glxxxxxxxxxxxxxxxxxxxxxx
  OPENCODE_MODEL=opencode-go/deepseek-v4-pro
""")
    sys.exit(1)


def main():
    args = sys.argv[1:]
    if not args:
        print_usage()

    cmd = args[0]
    remaining = args[1:]

    if cmd == "init":
        orch = Orchestrator(ORCHESTRATOR_CONFIG)
        orch.init()
        return

    if cmd == "init-repos":
        import_repos_from_config(ORCHESTRATOR_CONFIG)
        gitlab_host = os.getenv("GITLAB_HOST", os.getenv("GIT_HOST", ""))
        gitlab_token = os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))
        if not gitlab_host or not gitlab_token:
            _struct_log.error("GITLAB_HOST and GITLAB_TOKEN must be set")
            sys.exit(1)

        from gitlab_client import gitlab_get_sync
        projects = gitlab_get_sync("/projects", {
            "membership": "true",
            "min_access_level": "20",
            "order_by": "name",
        }, gitlab_host, gitlab_token)
        if projects is None:
            _struct_log.error("GitLab API error")
            sys.exit(1)

        from database import get_all_repos as _get_all_repos, add_repo as _add_repo, get_repo as _get_repo
        existing = {r["name"] for r in _get_all_repos()}
        added = 0
        for p in projects:
            name = p.get("name", "")
            if not name or name in existing:
                continue
            url = p.get("http_url_to_repo", "")
            branch = p.get("default_branch", "development")
            description = p.get("description", "")
            topics = p.get("topics", [])
            _add_repo(name=name, url=url, branch=branch, description=description, tags=topics)
            existing.add(name)
            added += 1
            _struct_log.info(f"Imported repo: {name}: {description[:60]}")

        _struct_log.info(f"{added} repos imported, {len(projects) - added} already existing")
        return

    if cmd == "update":
        orch = Orchestrator(ORCHESTRATOR_CONFIG)
        orch.update()
        return

    if cmd == "serve":
        import uvicorn
        raw_port = os.getenv("ORCHESTRATOR_PORT", "8080")
        if raw_port.startswith("tcp://"):
            raw_port = raw_port.split(":")[-1]
        PORT = int(raw_port)
        _struct_log.info(f"Orchestrator FastAPI server on port {PORT}")
        uvicorn.run("server:app", host="0.0.0.0", port=PORT, reload=False)
        return

    if cmd == "process":
        if not remaining:
            _struct_log.error("Missing ticket argument")
            print_usage()

        ticket_path = remaining[0]
        flags = set(remaining[1:])
        use_llm = "--llm" in flags or "--llm-only" in flags
        skip_clone = "--no-clone" in flags

        if not Path(ticket_path).is_file():
            _struct_log.error(f"File not found: {ticket_path}")
            sys.exit(1)

        ticket = Ticket.from_json(ticket_path)
        _struct_log.info(f"Ticket loaded: {ticket.id} – {ticket.title}")

        orch = Orchestrator(ORCHESTRATOR_CONFIG)
        orch.init()
        orch.process(ticket, use_llm=use_llm, skip_clone=skip_clone)
        return

    # Legacy: ticket file directly as first argument
    if Path(cmd).is_file():
        flags = set(remaining)
        ticket = Ticket.from_json(cmd)
        _struct_log.info(f"Ticket loaded: {ticket.id} – {ticket.title}")
        orch = Orchestrator(ORCHESTRATOR_CONFIG)
        orch.init()
        orch.process(ticket, use_llm="--llm" in flags, skip_clone="--no-clone" in flags)
        return

    print_usage()


if __name__ == "__main__":
    main()