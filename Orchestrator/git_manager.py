"""
Git repository manager, data classes, and LeanKG manager.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from database import get_setting, get_all_repos, import_repos_from_config

_struct_log = logging.getLogger("hivemind.main")


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
                _struct_log.error(f"{config.name}: fetch failed")
                return status

            rc, local_commit, _ = self._run("git", "rev-parse", f"heads/{branch}", cwd=str(repo_dir))
            rc2, remote_commit, _ = self._run("git", "rev-parse", f"origin/{branch}", cwd=str(repo_dir))
            status.latest_commit = remote_commit or local_commit

            if local_commit != remote_commit:
                status.changes_detected = True
                rc, _, err = self._run("git", "reset", "--hard", f"origin/{branch}", cwd=str(repo_dir))
                if rc:
                    status.error = f"pull failed: {err}"
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
                    rc, out, err = self._run("git", "clone", "--depth=100", "-b", fallback_branch, url, str(repo_dir))
                    if rc == 0:
                        break
            if rc != 0:
                status.error = f"Clone failed: {err}"
                return status
            rc, commit, _ = self._run("git", "rev-parse", "HEAD", cwd=str(repo_dir))
            status.latest_commit = commit
            status.exists = True

        return status

    async def aensure_repo(self, config: RepoConfig) -> RepoStatus:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.ensure_repo, config)

    def init_all(self, repos: List[RepoConfig]) -> List[RepoStatus]:
        return [self.ensure_repo(r) for r in repos]

    def update_all(self, repos: List[RepoConfig]) -> List[RepoStatus]:
        return [self.ensure_repo(r) for r in repos]

    async def ainit_all(self, repos: List[RepoConfig]) -> List[RepoStatus]:
        loop = asyncio.get_event_loop()
        tasks = [loop.run_in_executor(None, self.ensure_repo, r) for r in repos]
        return await asyncio.gather(*tasks)

    async def aupdate_all(self, repos: List[RepoConfig]) -> List[RepoStatus]:
        loop = asyncio.get_event_loop()
        tasks = [loop.run_in_executor(None, self.ensure_repo, r) for r in repos]
        return await asyncio.gather(*tasks)


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
            _struct_log.warning("LeanKG CLI not available → skipping indexing (repos are still cloned)")
            return
        for s in statuses:
            if s.error:
                continue
            if self.index_repo(s.local_path):
                s.leankg_ready = True


__all__ = [
    "RepoConfig", "OrchestratorConfig", "RepoStatus", "Ticket",
    "RepoManager", "LeanKGManager",
]