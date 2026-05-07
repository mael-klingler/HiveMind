# Copyright 2026 Mael Klingler
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
WorkspaceBuilder – builds workspaces and spawns K8s pods when a ticket starts.
"""

import asyncio
import logging
import os
import traceback
from pathlib import Path
from typing import Optional

from database import (
    get_all_repos, get_setting, set_ticket_ai_planning, update_ticket_description,
    update_ticket_llm_usage,
)
from config import (
    ORCHESTRATOR_CONFIG, OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_TIMEOUT,
    OLLAMA_CLOUD_API_KEY, AGENT_MAX_RETRIES,
)

_main_module = None


def _get_main():
    global _main_module
    if _main_module is None:
        import main as _m
        _main_module = _m
    return _main_module


log = logging.getLogger("hivemind")


class WorkspaceBuilder:
    def __init__(self):
        self._main = _get_main()
        self.config = self._main.OrchestratorConfig.from_file(
            self._main.ORCHESTRATOR_CONFIG
        )

        db_ollama_host = get_setting("ollama_host")
        db_ollama_model = get_setting("ollama_model")
        if db_ollama_host:
            self.config.ollama_host = db_ollama_host
        if db_ollama_model:
            self.config.ollama_model = db_ollama_model

        Path(self.config.pvc_mount_path).mkdir(parents=True, exist_ok=True)
        self.git = self._main.RepoManager(self.config.pvc_mount_path, self.config.track_branch, self.config.branch_fallback_order)
        self.leankg = self._main.LeanKGManager(self.config)
        self.llm = self._main.OllamaClient(self.config.ollama_host, self.config.ollama_model)
        self._statuses = []
        self._init_done = False
        self._init_lock = asyncio.Lock()

    @property
    def repositories(self):
        return [self._main.RepoConfig.from_dict(r) for r in get_all_repos(active_only=True)]

    def _ensure_init(self):
        if self._init_done:
            return
        import threading
        if not hasattr(self, '_init_thread_lock'):
            self._init_thread_lock = threading.Lock()
        with self._init_thread_lock:
            if self._init_done:
                return
            self._main.configure_git_credentials()
            self._statuses = self.git.update_all(self.repositories)
            if self.config.leankg_enabled:
                self.leankg.index_all(self._statuses)
            self._init_done = True

    async def _aensure_init(self):
        if self._init_done:
            return
        async with self._init_lock:
            if self._init_done:
                return
            log.info("Initializing repos (first request)...")
            self._main.configure_git_credentials()
            repos = self.repositories
            for r in repos:
                try:
                    status = await self.git.aensure_repo(r)
                    self._statuses.append(status)
                    log.info(f"{r.name}: initialized")
                except Exception as e:
                    log.error(f"{r.name}: init failed: {e}")
            if self.config.leankg_enabled:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self.leankg.index_all, self._statuses)
            self._init_done = True
            log.info("Repo initialization complete")

    async def build_and_spawn(self, ticket):
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._build_and_spawn_sync, ticket)
        except Exception as e:
            log.error(f"Workspace-Builder error: {e}", exc_info=True)
            traceback.print_exc()
            return f"failed: {e}", None, None

    def _build_and_spawn_sync(self, ticket):
        self._ensure_init()

        manual_repos = getattr(ticket, 'selected_repos', None) or []

        if manual_repos:
            log.info(f"Ticket {ticket.id} has manual repo selection", extra={"ticket_id": ticket.id})
            selected_configs = [r for r in self.repositories if r.name in manual_repos]
            if not selected_configs:
                selected_configs = [r for r in self.repositories if r.active]
                if selected_configs:
                    log.warning(f"Manual repos {manual_repos} not found, falling back to all active repos for ticket {ticket.id}", extra={"ticket_id": ticket.id})
                    manual_repos = [r.name for r in selected_configs]
                else:
                    log.error(f"No matching repositories for ticket {ticket.id}", extra={"ticket_id": ticket.id})
                    return "failed: no matching repositories", None, None

            repo_context_parts = []
            for r in selected_configs:
                desc = r.description or "No description"
                tags = ", ".join(r.tags) if r.tags else ""
                repo_context_parts.append(f"- **{r.name}**: {desc}" + (f" (Tags: {tags})" if tags else ""))
            repo_context = "\n".join(repo_context_parts)
            if "**Repositories selected:**" not in ticket.description and "**Repositories:**" not in ticket.description:
                enriched_description = f"{ticket.description}\n\n---\n**Repositories selected:**\n{repo_context}"
            else:
                enriched_description = ticket.description
            update_ticket_description(ticket.id, enriched_description)
            ticket.description = enriched_description
            analysis = {
                "selected_repos": manual_repos,
                "primary_repo": manual_repos[0],
                "complexity": "Medium",
                "estimated_hours": 2,
                "reasoning": "Repositories manually selected by user",
            }
            selected_names = set(manual_repos)
        else:
            analysis = None
            max_llm_retries = 3
            retry_delays = [10, 30, 60]

            if self.llm.is_available():
                for attempt in range(max_llm_retries):
                    try:
                        analysis = self.llm.analyze_repos_for_ticket(
                            ticket, self._statuses, self.leankg
                        )
                        if analysis:
                            break
                    except RuntimeError as e:
                        err_str = str(e)
                        if attempt < max_llm_retries - 1:
                            delay = retry_delays[attempt]
                            log.warning(f"LLM analysis failed for ticket {ticket.id}: {e}", extra={"ticket_id": ticket.id, "attempt": attempt+1})
                            log.warning(f"LLM retry in {delay}s...", extra={"ticket_id": ticket.id, "attempt": attempt+1})
                            import time
                            time.sleep(delay)
                        else:
                            log.error(f"LLM analysis failed after {max_llm_retries} attempts for ticket {ticket.id}: {e}", extra={"ticket_id": ticket.id})
            else:
                log.error(f"Ollama not reachable ({self.llm.host}) – ticket {ticket.id}", extra={"ticket_id": ticket.id})

            if not analysis:
                log.error(f"No AI analysis for ticket {ticket.id}", extra={"ticket_id": ticket.id})
                return "failed: no llm analysis", None, None

            selected_names = set(analysis.get("selected_repos", []))
            selected_configs = [r for r in self.repositories if r.name in selected_names]
            if not selected_configs and self.repositories:
                log.warning(f"LLM returned no matching repos for ticket {ticket.id}, falling back to all active repos", extra={"ticket_id": ticket.id})
                selected_configs = [r for r in self.repositories if r.active]
                analysis["selected_repos"] = [r.name for r in selected_configs]
                analysis["primary_repo"] = selected_configs[0].name if selected_configs else None
            if not selected_configs:
                log.error(f"No matching repositories for ticket {ticket.id}", extra={"ticket_id": ticket.id})
                return "failed: no matching repositories", None, None

            repo_context_parts = []
            for r in selected_configs:
                desc = r.description or "No description"
                tags = ", ".join(r.tags) if r.tags else ""
                repo_context_parts.append(f"- **{r.name}**: {desc}" + (f" (Tags: {tags})" if tags else ""))
            repo_context = "\n".join(repo_context_parts)
            reasoning = analysis.get("reasoning", "")
            if "**Repositories:**" not in ticket.description and "**Repositories selected:**" not in ticket.description:
                enriched_description = f"{ticket.description}\n\n---\n**Repositories:**\n{repo_context}\n\n**AI Assessment:** {reasoning}"
            else:
                enriched_description = ticket.description
            update_ticket_description(ticket.id, enriched_description)
            ticket.description = enriched_description

        prompt = self._main.generate_assignment_prompt(ticket, analysis, selected_configs)

        retry_ctx = getattr(self, '_retry_context', {})
        if retry_ctx:
            analysis.update(retry_ctx)
            prompt = self._main.generate_assignment_prompt(ticket, analysis, selected_configs)

        set_ticket_ai_planning(ticket.id, analysis)

        llm_usage = analysis.pop("_llm_usage", None)
        if llm_usage:
            update_ticket_llm_usage(
                ticket.id,
                prompt_tokens=llm_usage.get("prompt_tokens", 0),
                completion_tokens=llm_usage.get("completion_tokens", 0),
                cost_usd=0.0,
                model=llm_usage.get("model", ""),
            )

        workspace_dir = Path(self.config.work_dir) / f"workspace_{ticket.id}"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        self._main.create_opencode_config(workspace_dir / ".opencode", ticket, selected_configs, analysis, prompt)
        self._main.create_launch_scripts(workspace_dir)

        self._main.spawn_agent_pod(ticket, selected_configs, prompt, analysis)

        pod_name = f"agent-worker-{ticket.id.lower()}"
        os.environ[f"AGENT_POD_{ticket.id}"] = pod_name

        return "running", workspace_dir, pod_name


__all__ = ["WorkspaceBuilder"]