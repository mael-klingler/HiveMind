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

#!/usr/bin/env python3
"""
GitLab VCS provider – implements VCSProvider for GitLab.
"""

import json
import logging
import os
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

from .base import VCSProvider

log = logging.getLogger("hivemind.gitlab")


class GitLabProvider(VCSProvider):
    _async_client: Optional[httpx.AsyncClient] = None
    _sync_client: Optional[httpx.Client] = None

    @property
    def name(self) -> str:
        return "gitlab"

    @property
    def token_env_key(self) -> str:
        return "GITLAB_TOKEN"

    @property
    def host_env_key(self) -> str:
        return "GITLAB_HOST"

    def get_token(self) -> str:
        return os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))

    def get_host(self) -> str:
        return os.getenv("GITLAB_HOST", os.getenv("GIT_HOST", ""))

    def auth_headers(self, token: str = None) -> Dict[str, str]:
        t = token or self.get_token()
        headers = {"Content-Type": "application/json"}
        if t:
            headers["PRIVATE-TOKEN"] = t
        return headers

    def parse_mr_url(self, mr_url: str) -> Tuple[Optional[str], Optional[str]]:
        if not mr_url:
            return None, None
        try:
            if "/-/merge_requests/" in mr_url:
                parts = mr_url.split("/-/merge_requests/")
            else:
                parts = mr_url.split("/merge_requests/")
            if len(parts) < 2:
                return None, None
            project_path = parts[0].replace("https://", "").replace("http://", "")
            if "/" in project_path:
                project_path = project_path.split("/", 1)[1]
            mr_iid = parts[1].split("/")[0]
            return project_path, mr_iid
        except (ValueError, IndexError):
            return None, None

    def get_default_git_user(self) -> str:
        return "gitlab-ci-token"

    def extract_ticket_id_from_branch(self, branch: str) -> Optional[str]:
        for prefix in ("PROJ-", "BUG-", "TASK-", "GL-"):
            m = re.search(rf'{prefix}\d+', branch, re.IGNORECASE)
            if m:
                return m.group(0).upper()
        return None

    def get_branch_list_url(self, project_path: str) -> str:
        host = self.get_host()
        encoded = quote(project_path, safe="")
        return f"https://{host}/{project_path}/-/branches"

    def parse_webhook_event(self, payload: Dict, headers: Dict) -> Optional[Dict]:
        event_type = headers.get("X-Gitlab-Event", "")
        if event_type == "Issue Hook":
            obj_kind = payload.get("object_kind", "")
            action = payload.get("object_attributes", {}).get("action", "")
            if obj_kind == "issue" and action in ("open", "update", "reopen"):
                attrs = payload.get("object_attributes", {})
                return {
                    "type": "issue",
                    "action": action,
                    "project_id": payload.get("project", {}).get("id"),
                    "project_path": payload.get("project", {}).get("path_with_namespace", ""),
                    "iid": attrs.get("iid"),
                    "title": attrs.get("title", ""),
                    "description": attrs.get("description", ""),
                    "url": attrs.get("url", ""),
                    "labels": [lb.get("title", "") for lb in payload.get("labels", [])],
                    "raw": payload,
                }
        elif event_type == "Merge Request Hook":
            attrs = payload.get("object_attributes", {})
            action = attrs.get("action", "")
            if action in ("open", "update", "reopen", "merge", "close", "approval"):
                return {
                    "type": "merge_request",
                    "action": action,
                    "project_id": payload.get("project", {}).get("id"),
                    "project_path": payload.get("project", {}).get("path_with_namespace", ""),
                    "iid": attrs.get("iid"),
                    "title": attrs.get("title", ""),
                    "url": attrs.get("url", ""),
                    "state": attrs.get("state", ""),
                    "source_branch": attrs.get("source_branch", ""),
                    "target_branch": attrs.get("target_branch", ""),
                    "raw": payload,
                }
        return None

    async def _get_async_client(self) -> httpx.AsyncClient:
        if GitLabProvider._async_client is None or GitLabProvider._async_client.is_closed:
            GitLabProvider._async_client = httpx.AsyncClient(timeout=30.0)
        return GitLabProvider._async_client

    async def close_async_client(self):
        if GitLabProvider._async_client and not GitLabProvider._async_client.is_closed:
            await GitLabProvider._async_client.aclose()
            GitLabProvider._async_client = None

    def _get_sync_client(self) -> httpx.Client:
        if GitLabProvider._sync_client is None or GitLabProvider._sync_client.is_closed:
            GitLabProvider._sync_client = httpx.Client(timeout=30.0)
        return GitLabProvider._sync_client

    def close_sync_client(self):
        if GitLabProvider._sync_client and not GitLabProvider._sync_client.is_closed:
            GitLabProvider._sync_client.close()
            GitLabProvider._sync_client = None

    async def _gitlab_get(self, path: str, params: Dict = None, host: str = None, token: str = None) -> Optional[List[Dict]]:
        host = host or self.get_host()
        token = token or self.get_token()
        if not host or not token:
            return None
        client = await self._get_async_client()
        base_url = f"https://{host}/api/v4{path}"
        headers = self.auth_headers(token)
        all_items = []
        page = 1
        while True:
            query = dict(params or {})
            query["page"] = page
            query["per_page"] = 100
            try:
                resp = await client.get(base_url, params=query, headers=headers)
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                items = resp.json()
                if not items:
                    break
                all_items.extend(items)
                total_pages = int(resp.headers.get("X-Total-Pages", "1"))
                if page >= total_pages:
                    break
                page += 1
            except (httpx.HTTPStatusError, httpx.RequestError):
                break
        return all_items if all_items else None

    async def _gitlab_get_single(self, path: str, host: str = None, token: str = None) -> Optional[Dict]:
        host = host or self.get_host()
        token = token or self.get_token()
        if not host or not token:
            return None
        client = await self._get_async_client()
        url = f"https://{host}/api/v4{path}"
        headers = self.auth_headers(token)
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError):
            return None

    def _gitlab_get_sync(self, path: str, params: Dict = None, host: str = None, token: str = None) -> Optional[List[Dict]]:
        host = host or self.get_host()
        token = token or self.get_token()
        if not host or not token:
            return None
        client = self._get_sync_client()
        base_url = f"https://{host}/api/v4{path}"
        headers = self.auth_headers(token)
        all_items = []
        page = 1
        while True:
            query = dict(params or {})
            query["page"] = page
            query["per_page"] = 100
            try:
                resp = client.get(base_url, params=query, headers=headers)
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                items = resp.json()
                if not items:
                    break
                all_items.extend(items)
                total_pages = int(resp.headers.get("X-Total-Pages", "1"))
                if page >= total_pages:
                    break
                page += 1
            except (httpx.HTTPStatusError, httpx.RequestError):
                break
        return all_items if all_items else None

    def _gitlab_get_single_sync(self, path: str, host: str = None, token: str = None) -> Optional[Dict]:
        host = host or self.get_host()
        token = token or self.get_token()
        if not host or not token:
            return None
        client = self._get_sync_client()
        url = f"https://{host}/api/v4{path}"
        headers = self.auth_headers(token)
        try:
            resp = client.get(url, headers=headers)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError):
            return None

    async def _gitlab_post(self, path: str, body: Dict = None, host: str = None, token: str = None) -> Optional[Dict]:
        host = host or self.get_host()
        token = token or self.get_token()
        if not host or not token:
            return None
        client = await self._get_async_client()
        url = f"https://{host}/api/v4{path}"
        headers = self.auth_headers(token)
        try:
            resp = await client.post(url, json=body or {}, headers=headers)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError):
            return None

    async def fetch_mr(self, mr_url: str) -> Optional[Dict]:
        project_path, mr_iid = self.parse_mr_url(mr_url)
        if not project_path or not mr_iid:
            return None
        encoded_path = project_path.replace("/", "%2F")
        path = f"/projects/{encoded_path}/merge_requests/{mr_iid}"
        return await self._gitlab_get_single(path)

    async def fetch_mr_comments(self, project_path: str, mr_iid: str) -> Optional[List[Dict]]:
        encoded_path = project_path.replace("/", "%2F")
        path = f"/projects/{encoded_path}/merge_requests/{mr_iid}/notes"
        return await self._gitlab_get(path, {"sort": "asc", "per_page": "50"})

    async def search_open_mrs(self, project_path: str, source_branch: str) -> Optional[List[Dict]]:
        encoded_path = project_path.replace("/", "%2F")
        path = f"/projects/{encoded_path}/merge_requests"
        return await self._gitlab_get(path, {"state": "opened", "source_branch": source_branch})

    async def create_mr(self, project_path: str, source_branch: str, target_branch: str, title: str, description: str = "") -> Optional[Dict]:
        encoded_path = project_path.replace("/", "%2F")
        path = f"/projects/{encoded_path}/merge_requests"
        body = {
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": title,
            "description": description,
            "remove_source_branch": True,
        }
        return await self._gitlab_post(path, body)

    async def fetch_mr_approvals(self, project_path: str, mr_iid: str) -> Optional[Dict]:
        encoded_path = project_path.replace("/", "%2F")
        path = f"/projects/{encoded_path}/merge_requests/{mr_iid}/approvals"
        return await self._gitlab_get_single(path)

    async def list_branches(self, project_path: str) -> Optional[List[Dict]]:
        encoded_path = project_path.replace("/", "%2F")
        path = f"/projects/{encoded_path}/repository/branches"
        return await self._gitlab_get(path)

    async def list_projects(self, **kwargs) -> Optional[List[Dict]]:
        path = "/projects"
        params = {"membership": "true", "min_access_level": "20"}
        params.update(kwargs)
        return await self._gitlab_get(path, params)

    async def create_project_hook(self, project_path: str, hook_config: Dict, gitlab_host: str = None, gitlab_token: str = None) -> Optional[Dict]:
        encoded_path = project_path.replace("/", "%2F") if "/" in project_path else project_path
        path = f"/projects/{encoded_path}/hooks"
        return await self._gitlab_post(path, hook_config, gitlab_host, gitlab_token)