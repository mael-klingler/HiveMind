#!/usr/bin/env python3

# Copyright 2025 Mael Klingler
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Orchestrator – K8s/PVC-ready Workspace Generator
CLI entry point + Orchestrator class.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import (
    ORCHESTRATOR_CONFIG, OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_TIMEOUT,
    OPENCODE_MODEL, OLLAMA_BASE_URL, AGENT_NAMESPACE, AGENT_IMAGE,
    GITLAB_HOST, GITLAB_TOKEN, OPENCODE_PORT, OLLAMA_CLOUD_API_KEY,
)
from database import (
    set_ticket_ai_planning, get_enabled_mcp_servers, get_enabled_agent_instructions,
    get_agent_mcp_servers, get_agent_assigned_instructions, get_enabled_plugin_names,
    get_agent_memory_as_markdown, get_setting, import_repos_from_config,
    get_all_repos,
)
from git_manager import RepoConfig, OrchestratorConfig, RepoStatus, Ticket, RepoManager, LeanKGManager
from llm import OllamaClient
from git_credentials import configure_git_credentials
from workspace_utils import (
    branch_name_for, create_opencode_config, create_launch_scripts,
    generate_assignment_prompt,
)
from logging_setup import log as _struct_log

_struct_log = logging.getLogger("hivemind.main")


def _load_dotenv(path: str = "/app/config/.env"):
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


def _ai_enrich_repo(repo_info: Dict) -> Dict:
    from config import OLLAMA_HOST as _oh, OLLAMA_MODEL as _om
    llm = OllamaClient(_oh, _om)
    if not llm.is_available():
        return repo_info
    try:
        prompt = json.dumps({
            "instruction": "Enrich this repo entry with a better description and relevant tags. Return JSON with: name, url, description, tags.",
            "repo": repo_info,
        }, ensure_ascii=False)
        result = llm._post({"model": llm.model, "messages": [{"role": "user", "content": prompt}], "stream": False, "format": "json"})
        content = result.get("message", {}).get("content", "") or result.get("choices", [{}])[0].get("message", {}).get("content", "")
        if content:
            enriched = json.loads(content) if isinstance(content, str) else content
            repo_info.update({k: v for k, v in enriched.items() if v})
    except Exception:
        pass
    return repo_info


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
        gitlab_host_v = os.getenv("GITLAB_HOST", os.getenv("GIT_HOST", ""))
        gitlab_token_v = os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))
        if not gitlab_host_v or not gitlab_token_v:
            _struct_log.error("GITLAB_HOST and GITLAB_TOKEN must be set")
            sys.exit(1)

        from gitlab_client import gitlab_get_sync
        projects = gitlab_get_sync("/projects", {
            "membership": "true",
            "min_access_level": "20",
            "order_by": "name",
        }, gitlab_host_v, gitlab_token_v)
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