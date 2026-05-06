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
API routes: Repos
"""

import asyncio
import os
import signal as _signal
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request

from database import (
    db_add_repo,
    db_delete_repo,
    db_update_repo,
    get_all_repos,
    get_repo,
    get_setting,
    import_repos_from_config,
    set_repo_active,
)
from background.sse import broadcast_event
from background.queue_processor import _get_worker

router = APIRouter()


@router.get("/api/repos")
def api_get_repos():
    return get_all_repos()


@router.get("/api/repo-names")
def api_repo_names():
    return [r["name"] for r in get_all_repos(active_only=True)]


@router.get("/api/repos/{name}/branches")
async def api_repo_branches(name: str):
    repo = get_repo(name)
    if not repo:
        raise HTTPException(status_code=404, detail=f"Repository '{name}' not found")

    branches = set()
    default_branch = repo.get("branch", "development")

    gitlab_token = os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))
    gitlab_host = os.getenv("GITLAB_HOST", os.getenv("GIT_HOST", ""))

    if gitlab_token and gitlab_host:
        from gitlab_client import gitlab_get
        url = repo.get("url", "")
        project_path = ""
        if "://" in url:
            project_path = url.split("://")[-1].replace(".git", "")
            if "/" in project_path and ":" in project_path.split("/")[0]:
                project_path = "/".join(project_path.split("/")[1:])

        if project_path:
            encoded_path = project_path.replace("/", "%2F")
            try:
                branch_list = await gitlab_get(f"/projects/{encoded_path}/repository/branches", {"per_page": "100"}, gitlab_host, gitlab_token)
                if branch_list:
                    for b in branch_list:
                        branches.add(b.get("name", ""))
            except Exception:
                pass

    repo_dir = None
    try:
        w = _get_worker()
        repo_dir = Path(w.config.pvc_mount_path) / name
    except Exception:
        pass
    if repo_dir and repo_dir.exists() and (repo_dir / ".git").exists():
        try:
            result = subprocess.run(
                ["git", "branch", "-r", "--format=%(refname:strip=3)"],
                capture_output=True, text=True, cwd=str(repo_dir), timeout=15
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    line = line.strip()
                    if line and "HEAD" not in line:
                        branches.add(line)
        except Exception:
            pass

    if default_branch:
        branches.add(default_branch)
    for fb in ("development", "qa", "main", "master", "staging", "production"):
        branches.add(fb)

    branch_list = sorted(branches, key=lambda b: (b != default_branch, b.lower()))
    return {"branches": branch_list, "default": default_branch}


@router.post("/api/repos")
async def api_add_repo(req: Request):
    data = await req.json()
    name = data.get("name", "").strip()
    url = data.get("url", "").strip()
    if not name or not url:
        raise HTTPException(status_code=400, detail="name and url are required")

    branch = data.get("branch") or (get_setting("default_branch") or "development")
    tags = data.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    description = data.get("description", "")

    if get_repo(name):
        raise HTTPException(status_code=409, detail=f"Repository '{name}' already exists")

    ok = db_add_repo(name=name, url=url, branch=branch, description=description, tags=tags, active=1)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to add repository")

    from background import queue_processor
    queue_processor._worker = None

    return {"ok": True, "name": name}


@router.patch("/api/repos")
async def api_bulk_update_repos(req: Request):
    data = await req.json()
    branch = data.get("branch", "").strip()
    active = data.get("active")
    if not branch and active is None:
        raise HTTPException(status_code=400, detail="Provide 'branch' and/or 'active' to update")

    all_repos = get_all_repos()
    updated = []
    for repo in all_repos:
        fields = {}
        if branch:
            fields["branch"] = branch
        if active is not None:
            if isinstance(active, bool):
                fields["active"] = 1 if active else 0
            elif isinstance(active, int):
                fields["active"] = active
        if fields:
            db_update_repo(repo["name"], **fields)
            updated.append(repo["name"])

    from background import queue_processor
    queue_processor._worker = None

    return {"ok": True, "updated": updated, "count": len(updated)}


@router.delete("/api/repos/{name}")
def api_delete_repo(name: str):
    deleted = db_delete_repo(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Repository '{name}' not found")

    from background import queue_processor
    queue_processor._worker = None

    return {"ok": True, "deleted": name}


@router.put("/api/repos/{name}")
async def api_update_repo(name: str, req: Request):
    if not get_repo(name):
        raise HTTPException(status_code=404, detail=f"Repository '{name}' not found")
    data = await req.json()
    fields = {}
    for key in ("url", "branch", "description", "tags", "active"):
        if key in data:
            if key == "tags" and isinstance(data[key], str):
                fields[key] = [t.strip() for t in data[key].split(",") if t.strip()]
            else:
                fields[key] = data[key]
    ok = db_update_repo(name, **fields)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update repository")

    from background import queue_processor
    queue_processor._worker = None

    return {"ok": True, "name": name}


@router.post("/api/repos/{name}/activate")
async def api_activate_repo(name: str):
    if not get_repo(name):
        raise HTTPException(status_code=404, detail=f"Repository '{name}' not found")
    set_repo_active(name, True)

    from background import queue_processor
    queue_processor._worker = None

    return {"ok": True, "name": name, "active": True}


@router.post("/api/repos/{name}/deactivate")
async def api_deactivate_repo(name: str):
    if not get_repo(name):
        raise HTTPException(status_code=404, detail=f"Repository '{name}' not found")
    set_repo_active(name, False)

    from background import queue_processor
    queue_processor._worker = None

    return {"ok": True, "name": name, "active": False}


@router.get("/api/repos/gitlab-projects")
async def api_gitlab_projects():
    gitlab_host = os.getenv("GITLAB_HOST", os.getenv("GIT_HOST", ""))
    gitlab_token = os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))
    if not gitlab_host or not gitlab_token:
        raise HTTPException(status_code=400, detail="GITLAB_HOST and GITLAB_TOKEN required")

    from gitlab_client import gitlab_get_sync
    projects = gitlab_get_sync("/projects", {
        "membership": "true",
        "min_access_level": "20",
        "order_by": "name",
        "sort": "asc",
    }, gitlab_host, gitlab_token)
    if projects is None:
        raise HTTPException(status_code=502, detail="Failed to fetch projects from GitLab")

    existing_names = {r["name"] for r in get_all_repos()}

    result = []
    for p in projects:
        name = p.get("name", "")
        if not name:
            continue
        result.append({
            "name": name,
            "url": p.get("http_url_to_repo", ""),
            "default_branch": p.get("default_branch", "development"),
            "description": p.get("description", ""),
            "topics": p.get("topics", []),
            "already_imported": name in existing_names,
        })

    return result


@router.post("/api/repos/import-selected")
async def api_import_selected(req: Request):
    data = await req.json()
    selected = data.get("repos", [])
    if not selected:
        raise HTTPException(status_code=400, detail="No repos selected")

    existing_names = {r["name"] for r in get_all_repos()}

    added = []
    skipped = []
    for item in selected:
        name = item.get("name", "")
        url = item.get("url", "")
        if not name or not url:
            continue
        if name in existing_names:
            skipped.append(name)
            continue

        branch = item.get("branch") or item.get("default_branch", "development")
        description = item.get("description", "")
        tags = item.get("tags", item.get("topics", []))

        ok = db_add_repo(name=name, url=url, branch=branch, description=description, tags=tags, active=0)
        if ok:
            existing_names.add(name)
            added.append({"name": name})
        else:
            skipped.append(name)

    from background import queue_processor
    queue_processor._worker = None

    await broadcast_event("repos_updated", {"added": len(added), "skipped": len(skipped)})

    return {"ok": True, "added": added, "skipped": skipped}


@router.post("/api/repos/init-from-gitlab")
async def api_init_repos_from_gitlab(req: Request):
    data = {} if req is None else await req.json()
    use_ai = data.get("use_ai", True)
    min_access_level = data.get("min_access_level", 20)

    gitlab_host = os.getenv("GITLAB_HOST", os.getenv("GIT_HOST", ""))
    gitlab_token = os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))
    if not gitlab_host or not gitlab_token:
        raise HTTPException(status_code=400, detail="GITLAB_HOST and GITLAB_TOKEN required")

    from gitlab_client import gitlab_get_sync
    projects = gitlab_get_sync("/projects", {
        "membership": "true",
        "min_access_level": str(min_access_level),
        "order_by": "name",
        "sort": "asc",
    }, gitlab_host, gitlab_token)
    if projects is None:
        raise HTTPException(status_code=502, detail="Failed to fetch projects from GitLab")

    existing = get_all_repos()
    existing_names = {r["name"] for r in existing}

    added = []
    skipped = []
    for p in projects:
        name = p.get("name", "")
        url = p.get("http_url_to_repo", "")
        if not name or name in existing_names:
            skipped.append(name)
            continue

        repo_info = {
            "name": name,
            "url": url,
            "default_branch": p.get("default_branch", "development"),
            "description": p.get("description", ""),
            "topics": p.get("topics", []),
        }

        if use_ai:
            from main import _ai_enrich_repo
            repo_info = _ai_enrich_repo(repo_info)

        branch = repo_info.get("default_branch", "development")
        description = repo_info.get("description", "")
        tags = repo_info.get("tags", repo_info.get("topics", []))

        db_add_repo(name=name, url=url, branch=branch, description=description, tags=tags, active=0)
        added.append({"name": name, "description": description, "tags": tags})

    from background import queue_processor
    queue_processor._worker = None

    await broadcast_event("repos_updated", {"added": len(added), "skipped": len(skipped)})

    return {"ok": True, "added": added, "skipped": skipped, "total_projects": len(projects)}


@router.post("/api/restart")
async def api_restart():
    asyncio.get_event_loop().call_later(1, lambda: os.kill(os.getpid(), _signal.SIGTERM))
    return {"ok": True, "message": "Restarting in 1 second..."}