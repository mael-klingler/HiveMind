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
Workspace utilities: branch naming, opencode config, launch scripts, assignment prompt generation.
"""

import json
import os
import re
from pathlib import Path
from typing import Dict, List

from database import (
    get_setting, get_enabled_mcp_servers, get_enabled_agent_instructions,
    get_agent_mcp_servers, get_agent_assigned_instructions, get_enabled_plugin_names,
    get_agent_memory_as_markdown, set_ticket_ai_planning,
)
from config import OLLAMA_BASE_URL, OPENCODE_MODEL, OLLAMA_CLOUD_API_KEY, GITLAB_TOKEN, GITLAB_HOST
from git_manager import Ticket, RepoConfig


def branch_name_for(ticket) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', (ticket.title or "").lower()).strip('-')[:40]
    return f"feature/{ticket.id}-{slug}" if slug else f"feature/{ticket.id}"


def create_opencode_config(workspace_dir: Path, ticket: Ticket, selected: List[RepoConfig],
                           analysis: Dict, assignment_md: str):
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

    from logging_setup import log
    log.info("Workspace .opencode/ created")


def create_launch_scripts(workspace_dir: Path):
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
    from logging_setup import log
    log.info("Launch scripts created")


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

    prompt = f"""# 🎯 Task: {ticket.id} – {ticket.title}

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


__all__ = ["branch_name_for", "create_opencode_config", "create_launch_scripts", "generate_assignment_prompt"]