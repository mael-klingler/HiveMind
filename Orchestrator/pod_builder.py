#!/usr/bin/env python3

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
Pod builder – uses kubernetes Python client to build Pod specs
instead of f-string YAML templates.

Replaces:
  - Pod YAML as f-string template → kubernetes client V1Pod objects
  - GITLAB_TOKEN env var → K8s Secret reference
  - ConfigMap/Secret creation → kubernetes client API calls
  - _kubectl subprocess → k8s_client module
"""

import json
import os
import re
import logging
from pathlib import Path
from typing import Dict, List, Optional

from kubernetes import client as kclient

log = logging.getLogger("hivemind.pod")

AGENT_NAMESPACE = os.getenv("AGENT_NAMESPACE", "hivemind")
AGENT_IMAGE = os.getenv("AGENT_IMAGE", "hivemind-opencode:latest")


def build_pod_spec(
    ticket_id: str,
    ticket_title: str,
    repos: List[Dict],
    assignment_md: str,
    analysis: Dict,
    agent_id: str = "",
    queue_id: str = "",
    gitlab_host: str = "",
    git_user: str = "gitlab-ci-token",
    gitlab_token: str = "",
    ollama_base_url: str = "",
    opencode_model: str = "",
    ollama_cloud_api_key: str = "",
    plugin_names: List[str] = None,
    mcp_entries: Dict = None,
    branch: str = "",
) -> kclient.V1Pod:
    pod_name = f"agent-worker-{ticket_id.lower()}"
    has_ollama_secret = bool(ollama_cloud_api_key)

    repos_json = json.dumps({r["name"]: {"url": r["url"], "branch": r.get("branch", "development")} for r in repos}, indent=2, ensure_ascii=False)
    opencode_config = _build_opencode_config(opencode_model, ollama_base_url, plugin_names or [], mcp_entries or {})
    memory_md = analysis.get("_memory_md", "") or _default_memory_md()

    labels = {
        "app.kubernetes.io/name": "hivemind",
        "app.kubernetes.io/component": "agent",
        "ticket-id": ticket_id,
    }
    metadata = kclient.V1ObjectMeta(
        name=pod_name,
        namespace=AGENT_NAMESPACE,
        labels=labels,
    )

    init_container = kclient.V1Container(
        name="clone-repos",
        image=AGENT_IMAGE,
        image_pull_policy="IfNotPresent",
        volume_mounts=[
            kclient.V1VolumeMount(name="workspace", mount_path="/workspace"),
            kclient.V1VolumeMount(name="repos-config", mount_path="/config"),
        ],
        env=[
            kclient.V1EnvVar(name="GITLAB_HOST", value=gitlab_host),
            kclient.V1EnvVar(name="GITLAB_TOKEN", value_from=kclient.V1EnvVarSource(
                secret_key_ref=kclient.V1SecretKeySelector(
                    name="gitlab-token", key="token"
                )
            )),
            kclient.V1EnvVar(name="GIT_USER", value=git_user),
        ],
        command=["/bin/bash", "-c"],
        args=[_build_clone_script(repos_json)],
    )

    opencode_env = [
        kclient.V1EnvVar(name="GITLAB_TOKEN", value_from=kclient.V1EnvVarSource(
            secret_key_ref=kclient.V1SecretKeySelector(name="gitlab-token", key="token")
        )),
        kclient.V1EnvVar(name="GITLAB_HOST", value=gitlab_host),
        kclient.V1EnvVar(name="GIT_USER", value=git_user),
        kclient.V1EnvVar(name="GITLAB_USER", value=git_user),
        kclient.V1EnvVar(name="OLLAMA_BASE_URL", value=ollama_base_url),
        kclient.V1EnvVar(name="OPENCODE_MODEL", value=opencode_model),
        kclient.V1EnvVar(name="OPENCODE_PLUGINS", value=json.dumps(plugin_names or [])),
        kclient.V1EnvVar(name="QUEUE_ID", value=str(queue_id)),
        kclient.V1EnvVar(name="TICKET_ID", value=ticket_id),
        kclient.V1EnvVar(name="AGENT_ID", value=agent_id or ""),
        kclient.V1EnvVar(name="BRANCH", value=branch),
        kclient.V1EnvVar(name="OPENCODE_SERVER_PASSWORD", value=os.getenv("OPENCODE_SERVER_PASSWORD", "")),
        kclient.V1EnvVar(name="COMMENT_POLL_INTERVAL", value=os.getenv("COMMENT_POLL_INTERVAL", "30")),
            kclient.V1EnvVar(name="ORCHESTRATOR_URL", value=f"http://orchestrator.{AGENT_NAMESPACE}.svc.cluster.local:8080"),
            kclient.V1EnvVar(name="MODEL_ROUTING_ENABLED", value=os.getenv("MODEL_ROUTING_ENABLED", "false")),
            kclient.V1EnvVar(name="SIMPLE_MODEL", value=os.getenv("SIMPLE_MODEL", "")),
            kclient.V1EnvVar(name="COMPLEX_MODEL", value=os.getenv("COMPLEX_MODEL", "")),
        kclient.V1EnvVar(name="DRY_RUN", value="false"),
        kclient.V1EnvVar(name="OPENCODE_PERMISSION_WRITE", value="allow"),
        kclient.V1EnvVar(name="OPENCODE_PERMISSION_BASH", value="allow"),
        kclient.V1EnvVar(name="OPENCODE_PERMISSION_EXTERNAL_DIRECTORY", value="allow"),
        kclient.V1EnvVar(name="OPENCODE_PERMISSION_DOOM_LOOP", value="allow"),
        kclient.V1EnvVar(name="TEST_COMMAND", value=os.getenv("TEST_COMMAND", "")),
    ]

    if has_ollama_secret:
        opencode_env.append(kclient.V1EnvVar(
            name="OLLAMA_CLOUD_API_KEY",
            value_from=kclient.V1EnvVarSource(
                secret_key_ref=kclient.V1SecretKeySelector(name="ollama-cloud-api-key", key="api-key")
            )
        ))

    llm_provider = os.getenv("LLM_PROVIDER", "")
    if llm_provider:
        opencode_env.append(kclient.V1EnvVar(name="LLM_PROVIDER", value=llm_provider))

    openai_api_key = os.getenv("OPENAI_API_KEY", "")
    if openai_api_key:
        opencode_env.append(kclient.V1EnvVar(name="OPENAI_API_KEY", value=openai_api_key))

    openai_base_url = os.getenv("OPENAI_BASE_URL", "")
    if openai_base_url:
        opencode_env.append(kclient.V1EnvVar(name="OPENAI_BASE_URL", value=openai_base_url))

    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if anthropic_api_key:
        opencode_env.append(kclient.V1EnvVar(name="ANTHROPIC_API_KEY", value=anthropic_api_key))

    main_container = kclient.V1Container(
        name="opencode-agent",
        image=AGENT_IMAGE,
        image_pull_policy="IfNotPresent",
        volume_mounts=[
            kclient.V1VolumeMount(name="workspace", mount_path="/workspace"),
            kclient.V1VolumeMount(name="task-prompt", mount_path="/etc/task"),
            kclient.V1VolumeMount(name="opencode-config", mount_path="/mnt/opencode-config"),
            kclient.V1VolumeMount(name="memory-blocks", mount_path="/mnt/memory-blocks"),
        ],
        env=opencode_env,
        command=["/bin/bash", "-c"],
        args=[
            "set -e\n"
            "echo '🚀 Starting opencode agent for ticket $TICKET_ID'\n"
            "TASK_FILE=/etc/task/task.md\n"
            "if [ ! -f \"$TASK_FILE\" ]; then\n"
            "  echo '❌ No task file found at $TASK_FILE'\n"
            "  exit 1\n"
            "fi\n"
            "mkdir -p /home/hivemind/.config/opencode\n"
            "if [ -f /mnt/opencode-config/opencode.json ]; then\n"
            "  cp /mnt/opencode-config/opencode.json /home/hivemind/.config/opencode/opencode.json\n"
            "fi\n"
            "mkdir -p /home/hivemind/.config/opencode/memory\n"
            "if [ -d /mnt/memory-blocks ]; then\n"
            "  cp /mnt/memory-blocks/*.md /home/hivemind/.config/opencode/memory/ 2>/dev/null || true\n"
            "fi\n"
            "if [ -n \"${OLLAMA_CLOUD_API_KEY:-}\" ]; then\n"
            "  export OLLAMA_API_KEY=\"$OLLAMA_CLOUD_API_KEY\"\n"
            "fi\n"
            "export OPENCODE_CONFIG=/home/hivemind/.config/opencode/opencode.json\n"
            "export GITLAB_TOKEN GITLAB_HOST GIT_USER GITLAB_USER BRANCH\n"
            "export GITLAB_USER=${GITLAB_USER:-$GIT_USER}\n"
            "git config --global user.email 'hivemind-agents@example.com'\n"
            "git config --global user.name 'HiveMind'\n"
            "git config --global credential.helper store\n"
            "echo \"https://${GIT_USER}:${GITLAB_TOKEN}@${GITLAB_HOST}\" > /home/hivemind/.git-credentials\n"
            "chmod 600 /home/hivemind/.git-credentials\n"
            "for repo in $(jq -r 'keys[]' /config/repos.json); do\n"
            "  url=$(jq -r --arg r \"$repo\" '.[$r].url' /config/repos.json)\n"
            "  branch=$(jq -r --arg r \"$repo\" '.[$r].branch' /config/repos.json)\n"
            "  if echo \"$url\" | grep -qE '^https?://'; then\n"
            "    url=$(echo \"$url\" | sed -E \"s|^(https?://)|\\\\1${GIT_USER}:${GITLAB_TOKEN}@|\")\n"
            "  fi\n"
            "  if [ -d \"/workspace/$repo\" ] && [ -d \"/workspace/$repo/.git\" ]; then\n"
            "    cd \"/workspace/$repo\"\n"
            "  fi\n"
            "done\n"
            "PRIMARY_REPO=$(jq -r '.repositories[] | select(.primary == true) | .name' /home/hivemind/.config/opencode/opencode.json 2>/dev/null | head -1 || true)\n"
            "if [ -n \"$PRIMARY_REPO\" ] && [ -d \"/workspace/$PRIMARY_REPO\" ]; then\n"
            "  cd \"/workspace/$PRIMARY_REPO\"\n"
            "fi\n"
            "TASK_PROMPT=$(cat \"$TASK_FILE\")\n"
            "opencode web --hostname 0.0.0.0 --port 4096 &\n"
            "WEB_PID=$!\n"
            "sleep 3\n"
            "opencode run --attach \"http://localhost:4096\" ${OPENCODE_SERVER_PASSWORD:+--password \"$OPENCODE_SERVER_PASSWORD\"} --title \"[${TICKET_ID}] ${TICKET_TITLE:-}\" --dangerously-skip-permissions \"$TASK_PROMPT\" || true\n"
            "RC=$?\n"
            "echo \"🛑 opencode exited with code $RC\"\n"
            "LINES_ADDED=0\n"
            "LINES_REMOVED=0\n"
            "FILES_CHANGED=0\n"
            "for dir in /workspace/*/; do\n"
            "  repo=\"${dir%/}\"\n"
            "  [ -d \"$repo/.git\" ] || continue\n"
            "  cd \"$repo\" || continue\n"
            "  BRANCH_REF=$(git for-each-ref --format='%(upstream:short)' \"refs/heads/$(git branch --show-current 2>/dev/null)\" 2>/dev/null | sed 's|origin/||' || echo 'main')\n"
            "  [ -z \"$BRANCH_REF\" ] && BRANCH_REF='main'\n"
            "  STATS=$(git diff --numstat \"origin/$BRANCH_REF\"..HEAD 2>/dev/null || echo '')\n"
            "  if [ -n \"$STATS\" ]; then\n"
            "    while IFS=$'\\t' read -r ADD DEL FILE; do\n"
            "      LINES_ADDED=$((LINES_ADDED + ${ADD:-0}))\n"
            "      LINES_REMOVED=$((LINES_REMOVED + ${DEL:-0}))\n"
            "      FILES_CHANGED=$((FILES_CHANGED + 1))\n"
            "    done <<< \"$STATS\"\n"
            "  fi\n"
             "  cd /workspace || true\n"
             "done\n"
             "echo \"📊 Stats: +$LINES_ADDED -$LINES_REMOVED in $FILES_CHANGED files\"\n"
             "MR_URL=''\n"
             "for dir in /workspace/*/; do\n"
             "  repo=\"${dir%/}\"\n"
             "  [ -d \"$repo/.git\" ] || continue\n"
             "  cd \"$repo\" || continue\n"
             "  CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo '')\n"
             "  if [ -z \"$CURRENT_BRANCH\" ] || [ \"$CURRENT_BRANCH\" = \"main\" ] || [ \"$CURRENT_BRANCH\" = \"master\" ]; then\n"
             "    continue\n"
             "  fi\n"
             "  REMOTE_URL=$(git remote get-url origin 2>/dev/null || echo '')\n"
             "  PROJECT_PATH=$(echo \"$REMOTE_URL\" | sed -E 's|https?://[^@]+@||;s|\\.git$||;s|^[^/]+/||' 2>/dev/null || echo '')\n"
             "  if [ -z \"$PROJECT_PATH\" ]; then\n"
             "    REMOTE_URL=$(git remote get-url origin 2>/dev/null || echo '')\n"
             "    PROJECT_PATH=$(echo \"$REMOTE_URL\" | sed -E 's|https?://[^/]+/||;s|\\.git$||' 2>/dev/null || echo '')\n"
             "  fi\n"
             "  ENCODED_PATH=$(echo \"$PROJECT_PATH\" | sed 's|/|%2F|g' 2>/dev/null || echo '')\n"
             "  TARGET_BRANCH=$(jq -r --arg r \"$(basename $repo)\" '.[$r].branch' /config/repos.json 2>/dev/null || echo 'main')\n"
             "  if [ -z \"$TARGET_BRANCH\" ]; then\n"
             "    TARGET_BRANCH='main'\n"
             "  fi\n"
             "  echo \"🔀 Creating MR: $CURRENT_BRANCH -> $TARGET_BRANCH in $PROJECT_PATH\"\n"
             "  MR_RESPONSE=$(curl -s -X POST \"${GITLAB_HOST}/api/v4/projects/${ENCODED_PATH}/merge_requests\" \\\n"
             "    -H \"PRIVATE-TOKEN: ${GITLAB_TOKEN}\" \\\n"
             "    -H \"Content-Type: application/json\" \\\n"
             "    -d \"{\\\"source_branch\\\": \\\"${CURRENT_BRANCH}\\\", \\\"target_branch\\\": \\\"${TARGET_BRANCH}\\\", \\\"title\\\": \\\"[${TICKET_ID}] ${TICKET_TITLE:-Automated fix}\\\", \\\"remove_source_branch\\\": true}\" || echo '{}')\n"
             "  MR_WEB_URL=$(echo \"$MR_RESPONSE\" | jq -r '.web_url // empty' 2>/dev/null || echo '')\n"
             "  if [ -n \"$MR_WEB_URL\" ]; then\n"
             "    echo \"✅ MR created: $MR_WEB_URL\"\n"
             "    MR_URL=\"$MR_WEB_URL\"\n"
             "  else\n"
             "    echo \"⚠️ MR creation failed: $(echo $MR_RESPONSE | jq -r '.message // .error // \"unknown\"' 2>/dev/null || echo 'unknown')\"\n"
             "  fi\n"
             "  cd /workspace || true\n"
             "  break\n"
             "done\n"
             "echo '📡 Notifying orchestrator of completion...'\n"
             "AGENT_ID_VAL=\"${AGENT_ID:-$TICKET_ID}\"\n"
             "curl -s -X POST \"http://orchestrator." + AGENT_NAMESPACE + ".svc.cluster.local:8080/api/agents/$AGENT_ID_VAL/complete\" -H 'Content-Type: application/json' -d \"{\\\"agent_id\\\": \\\"$AGENT_ID_VAL\\\", \\\"ticket_id\\\": \\\"$TICKET_ID\\\", \\\"queue_id\\\": \\\"$QUEUE_ID\\\", \\\"lines_added\\\": $LINES_ADDED, \\\"lines_removed\\\": $LINES_REMOVED, \\\"files_changed\\\": $FILES_CHANGED, \\\"mr_url\\\": \\\"$MR_URL\\\"}\" || echo '⚠️ Failed to notify orchestrator'\n"
             "echo '✅ Completion notification sent'\n"
        ],
        ports=[kclient.V1ContainerPort(name="opencode-web", container_port=4096)],
        resources=kclient.V1ResourceRequirements(
            requests={"cpu": "500m", "memory": "1Gi"},
            limits={"cpu": "4", "memory": "8Gi"},
        ),
    )

    volumes = [
        kclient.V1Volume(name="workspace", empty_dir=kclient.V1EmptyDirVolumeSource()),
        kclient.V1Volume(name="repos-config", config_map=kclient.V1ConfigMapVolumeSource(name=f"{pod_name}-repos")),
        kclient.V1Volume(name="task-prompt", config_map=kclient.V1ConfigMapVolumeSource(name=f"{pod_name}-assignment")),
        kclient.V1Volume(name="opencode-config", config_map=kclient.V1ConfigMapVolumeSource(name=f"{pod_name}-opencode")),
        kclient.V1Volume(name="memory-blocks", config_map=kclient.V1ConfigMapVolumeSource(name=f"{pod_name}-memory")),
    ]

    spec = kclient.V1PodSpec(
        hostname=pod_name,
        subdomain="agent-session",
        restart_policy="Never",
        security_context=kclient.V1PodSecurityContext(
            run_as_non_root=True,
            run_as_user=1000,
            run_as_group=1000,
            fs_group=1000,
        ),
        volumes=volumes,
        init_containers=[init_container],
        containers=[main_container],
    )

    return kclient.V1Pod(metadata=metadata, spec=spec)


def _build_clone_script(repos_json: str) -> str:
    return """set -uo pipefail
FALLBACK_BRANCHES="development qa main master"
for repo in $(jq -r 'keys[]' /config/repos.json); do
  url=$(jq -r --arg r "$repo" '.[$r].url' /config/repos.json)
  branch=$(jq -r --arg r "$repo" '.[$r].branch' /config/repos.json)
  if echo "$url" | grep -qE "^https?://"; then
    url=$(echo "$url" | sed -E "s|^(https?://)|\\1${GIT_USER}:${GITLAB_TOKEN}@|")
  fi
  echo "Cloning $repo (branch: $branch) ..."
  if git clone -b "$branch" --single-branch "$url" "/workspace/$repo" 2>&1; then
    echo "✅ Cloned $repo on branch $branch"
  else
    echo "⚠️ Branch $branch not found for $repo, trying fallback branches..."
    CLONED=false
    for fb in $branch $FALLBACK_BRANCHES; do
      if [ "$fb" = "$branch" ]; then continue; fi
      rm -rf "/workspace/$repo" 2>/dev/null || true
      if git clone -b "$fb" --single-branch "$url" "/workspace/$repo" 2>&1; then
        echo "✅ Cloned $repo on fallback branch $fb"
        CLONED=true
        break
      fi
    done
    if [ "$CLONED" = "false" ]; then
      rm -rf "/workspace/$repo" 2>/dev/null || true
      echo "⚠️ No fallback branch worked for $repo, cloning default branch..."
      if git clone "$url" "/workspace/$repo" 2>&1; then
        echo "✅ Cloned $repo on default branch"
      else
        echo "❌ Failed to clone $repo – skipping"
        continue
      fi
    fi
  fi
  echo "Init leankg $repo ..."
  cd "/workspace/$repo"
  leankg init || echo "⚠️ leankg init failed for $repo"
  echo "Index leankg $repo ..."
  leankg index . || echo "⚠️ leankg index failed for $repo"
done
echo "All repos processed"
"""


def _build_opencode_config(opencode_model: str, ollama_base_url: str, plugin_names: List[str], mcp_entries: Dict) -> Dict:
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": f"ollama/{opencode_model}",
        "small_model": f"ollama/{opencode_model}",
        "autoupdate": False,
        "share": "disabled",
        "plugin": plugin_names,
        "provider": {
            "ollama": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Ollama",
                "options": {"baseURL": ollama_base_url},
                "models": {
                    opencode_model: {
                        "name": opencode_model,
                        "options": {"num_ctx": 32768},
                    }
                },
            }
        },
        "mcp": mcp_entries or {},
    }


def _default_memory_md() -> str:
    return """---
label: persona
description: Agent identity and behavior
limit: 5000
read_only: false
---
You are an autonomous software developer. Work carefully and methodically.

---
label: human
description: Operator preferences
limit: 5000
read_only: false
---
Prefer English UI language. Use Conventional Commits. Tests are mandatory.

---
label: project
description: Project conventions and architecture
limit: 5000
read_only: false
---
Tech-Stack: Vue 3 + TypeScript Frontend, Go Backend.
Tests: pnpm test && vue-tsc --noEmit (Frontend), go test ./... (Backend).
"""


def spawn_agent_pod(
    ticket_id: str,
    ticket_title: str,
    repos: List[Dict],
    assignment_md: str,
    analysis: Dict,
    agent_id: str = "",
    queue_id: str = "",
    mcp_servers: List[Dict] = None,
    plugin_names: List[str] = None,
    memory_md: str = "",
    ollama_base_url: str = "",
    opencode_model: str = "",
    ollama_cloud_api_key: str = "",
    gitlab_host: str = "",
    git_user: str = "gitlab-ci-token",
    gitlab_token: str = "",
) -> bool:
    from k8s_client import (
        ensure_namespace, create_configmap, replace_configmap, delete_pod,
        get_pod, create_namespaced_secret, get_secret, create_pod,
    )

    pod_name = f"agent-worker-{ticket_id.lower()}"

    ensure_namespace(AGENT_NAMESPACE)

    mcp_entries = {}
    if mcp_servers:
        for srv in mcp_servers:
            cmd = srv.get("command", "").split()
            entry = {"type": srv.get("server_type", "local"), "command": cmd, "enabled": True}
            args_raw = srv.get("args", "[]")
            if isinstance(args_raw, str):
                try:
                    args_list = json.loads(args_raw)
                    if args_list:
                        entry["args"] = args_list
                except (json.JSONDecodeError, TypeError):
                    pass
            env_raw = srv.get("env", "{}")
            if isinstance(env_raw, str):
                try:
                    env_dict = json.loads(env_raw)
                    if env_dict:
                        entry["environment"] = env_dict
                except (json.JSONDecodeError, TypeError):
                    pass
            mcp_entries[srv["name"]] = entry

    branch = analysis.get("branch", f"feature/{ticket_id.lower()}")
    opencode_config = _build_opencode_config(opencode_model, ollama_base_url, plugin_names or [], mcp_entries)

    repos_json = json.dumps({r["name"]: {"url": r["url"], "branch": r.get("branch", "development")} for r in repos}, indent=2, ensure_ascii=False)

    cm_labels = {"ticket-id": ticket_id}

    replace_configmap(f"{pod_name}-repos", {"repos.json": repos_json}, cm_labels, AGENT_NAMESPACE)
    log.info(f"ConfigMap {pod_name}-repos created")

    replace_configmap(f"{pod_name}-assignment", {"task.md": assignment_md}, cm_labels, AGENT_NAMESPACE)
    log.info(f"ConfigMap {pod_name}-assignment created")

    replace_configmap(f"{pod_name}-opencode", {"opencode.json": json.dumps(opencode_config, indent=2, ensure_ascii=False)}, cm_labels, AGENT_NAMESPACE)
    log.info(f"ConfigMap {pod_name}-opencode created")

    if not memory_md:
        memory_md = _default_memory_md()
    replace_configmap(f"{pod_name}-memory", {"memory.md": memory_md}, cm_labels, AGENT_NAMESPACE)
    log.info(f"ConfigMap {pod_name}-memory created")

    if ollama_cloud_api_key:
        try:
            create_namespaced_secret("ollama-cloud-api-key", {"api-key": ollama_cloud_api_key}, AGENT_NAMESPACE)
            log.info("Ollama Cloud Secret ensured")
        except Exception:
            pass

    if gitlab_token:
        try:
            create_namespaced_secret("gitlab-token", {"token": gitlab_token}, AGENT_NAMESPACE)
            log.info("GitLab token Secret ensured")
        except Exception:
            pass

    pod = build_pod_spec(
        ticket_id=ticket_id,
        ticket_title=ticket_title,
        repos=repos,
        assignment_md=assignment_md,
        analysis=analysis,
        agent_id=agent_id,
        queue_id=queue_id,
        gitlab_host=gitlab_host,
        git_user=git_user,
        gitlab_token=gitlab_token,
        ollama_base_url=ollama_base_url,
        opencode_model=opencode_model,
        ollama_cloud_api_key=ollama_cloud_api_key,
        plugin_names=plugin_names,
        mcp_entries=mcp_entries,
        branch=branch,
    )

    existing = get_pod(pod_name, AGENT_NAMESPACE)
    if existing:
        delete_pod(pod_name, AGENT_NAMESPACE)
        import time
        time.sleep(2)

    try:
        create_pod(pod, AGENT_NAMESPACE)
        log.info(f"Agent pod {pod_name} started")
    except Exception as e:
        log.error(f"Could not start agent pod: {e}")
        raise RuntimeError(f"Could not start agent pod: {e}")

    complexity = analysis.get("complexity", "Medium")
    primary = analysis.get("primary_repo", repos[0]["name"] if repos else "")
    log.info(f"Ticket: {ticket_id} – {ticket_title}")
    log.info(f"Complexity: {complexity} | Primary: {primary}")
    log.info(f"Repos ({len(repos)}): {', '.join(r['name'] for r in repos)}")
    log.info(f"Pod status: kubectl -n {AGENT_NAMESPACE} get pod {pod_name} -w")
    return True


def cleanup_agent_resources(ticket_id: str, namespace: str = None):
    from k8s_client import delete_configmap
    ns = namespace or AGENT_NAMESPACE
    pod_name = f"agent-worker-{ticket_id.lower()}"
    for suffix in ("repos", "assignment", "opencode", "memory"):
        cm_name = f"{pod_name}-{suffix}"
        delete_configmap(cm_name, ns)
    log.info(f"ConfigMaps cleaned up for {pod_name}")