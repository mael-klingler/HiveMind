#!/usr/bin/env python3
"""
GitHub VCS provider – implements VCSProvider for GitHub and GitHub Enterprise.
"""

import json
import logging
import os
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

from .base import VCSProvider

log = logging.getLogger("hivemind.github")


class GitHubProvider(VCSProvider):
    _async_client: Optional[httpx.AsyncClient] = None
    _sync_client: Optional[httpx.Client] = None

    @property
    def name(self) -> str:
        return "github"

    @property
    def token_env_key(self) -> str:
        return "GITHUB_TOKEN"

    @property
    def host_env_key(self) -> str:
        return "GITHUB_HOST"

    def get_token(self) -> str:
        return os.getenv("GITHUB_TOKEN", "")

    def get_host(self) -> str:
        return os.getenv("GITHUB_HOST", "github.com")

    def _base_url(self) -> str:
        host = self.get_host()
        if host == "github.com":
            return "https://api.github.com"
        return f"https://{host}/api/v3"

    def auth_headers(self, token: str = None) -> Dict[str, str]:
        t = token or self.get_token()
        headers = {"Content-Type": "application/json", "Accept": "application/vnd.github+json"}
        if t:
            headers["Authorization"] = f"Bearer {t}"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        return headers

    def parse_mr_url(self, mr_url: str) -> Tuple[Optional[str], Optional[str]]:
        if not mr_url:
            return None, None
        try:
            if "/pull/" not in mr_url:
                return None, None
            parts = mr_url.split("/pull/")
            if len(parts) < 2:
                return None, None
            repo_path = parts[0].replace("https://", "").replace("http://", "")
            if "/" in repo_path:
                repo_path = repo_path.split("/", 1)[1]
            pr_number = parts[1].split("/")[0].split("?")[0]
            return repo_path, pr_number
        except (ValueError, IndexError):
            return None, None

    def get_default_git_user(self) -> str:
        return "x-access-token"

    def extract_ticket_id_from_branch(self, branch: str) -> Optional[str]:
        for prefix in ("PROJ-", "BUG-", "TASK-", "GH-"):
            m = re.search(rf'{prefix}\d+', branch, re.IGNORECASE)
            if m:
                return m.group(0).upper()
        return None

    def get_branch_list_url(self, project_path: str) -> str:
        host = self.get_host()
        return f"https://{host}/{project_path}/branches"

    def parse_webhook_event(self, payload: Dict, headers: Dict) -> Optional[Dict]:
        event_type = headers.get("X-GitHub-Event", "")
        if event_type == "issues":
            action = payload.get("action", "")
            if action in ("opened", "edited", "reopened"):
                issue = payload.get("issue", {})
                repo = payload.get("repository", {})
                return {
                    "type": "issue",
                    "action": action.replace("opened", "open").replace("edited", "update").replace("reopened", "reopen"),
                    "project_id": repo.get("id"),
                    "project_path": repo.get("full_name", ""),
                    "iid": issue.get("number"),
                    "title": issue.get("title", ""),
                    "description": issue.get("body", ""),
                    "url": issue.get("html_url", ""),
                    "labels": [lb.get("name", "") for lb in issue.get("labels", [])],
                    "raw": payload,
                }
        elif event_type == "pull_request":
            action = payload.get("action", "")
            if action in ("opened", "synchronize", "reopened", "closed", "review_requested"):
                pr = payload.get("pull_request", {})
                repo = payload.get("repository", {})
                action_map = {
                    "opened": "open",
                    "synchronize": "update",
                    "reopened": "reopen",
                    "closed": "close",
                    "review_requested": "update",
                }
                return {
                    "type": "merge_request",
                    "action": action_map.get(action, action),
                    "project_id": repo.get("id"),
                    "project_path": repo.get("full_name", ""),
                    "iid": pr.get("number"),
                    "title": pr.get("title", ""),
                    "url": pr.get("html_url", ""),
                    "state": pr.get("state", ""),
                    "source_branch": pr.get("head", {}).get("ref", ""),
                    "target_branch": pr.get("base", {}).get("ref", ""),
                    "raw": payload,
                }
        return None

    async def _get_async_client(self) -> httpx.AsyncClient:
        if GitHubProvider._async_client is None or GitHubProvider._async_client.is_closed:
            GitHubProvider._async_client = httpx.AsyncClient(timeout=30.0)
        return GitHubProvider._async_client

    async def close_async_client(self):
        if GitHubProvider._async_client and not GitHubProvider._async_client.is_closed:
            await GitHubProvider._async_client.aclose()
            GitHubProvider._async_client = None

    def _get_sync_client(self) -> httpx.Client:
        if GitHubProvider._sync_client is None or GitHubProvider._sync_client.is_closed:
            GitHubProvider._sync_client = httpx.Client(timeout=30.0)
        return GitHubProvider._sync_client

    def close_sync_client(self):
        if GitHubProvider._sync_client and not GitHubProvider._sync_client.is_closed:
            GitHubProvider._sync_client.close()
            GitHubProvider._sync_client = None

    async def _github_get(self, path: str, params: Dict = None, host: str = None, token: str = None) -> Optional[List[Dict]]:
        t = token or self.get_token()
        base_url = f"{self._base_url()}{path}"
        headers = self.auth_headers(t)
        client = await self._get_async_client()
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
                if not isinstance(items, list) or not items:
                    break
                all_items.extend(items)
                link = resp.headers.get("Link", "")
                if 'rel="next"' not in link:
                    break
                page += 1
            except (httpx.HTTPStatusError, httpx.RequestError):
                break
        return all_items if all_items else None

    async def _github_get_single(self, path: str, token: str = None) -> Optional[Dict]:
        t = token or self.get_token()
        base_url = f"{self._base_url()}{path}"
        headers = self.auth_headers(t)
        client = await self._get_async_client()
        try:
            resp = await client.get(base_url, headers=headers)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError):
            return None

    def _github_get_sync(self, path: str, params: Dict = None, token: str = None) -> Optional[List[Dict]]:
        t = token or self.get_token()
        base_url = f"{self._base_url()}{path}"
        headers = self.auth_headers(t)
        client = self._get_sync_client()
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
                if not isinstance(items, list) or not items:
                    break
                all_items.extend(items)
                link = resp.headers.get("Link", "")
                if 'rel="next"' not in link:
                    break
                page += 1
            except (httpx.HTTPStatusError, httpx.RequestError):
                break
        return all_items if all_items else None

    def _github_get_single_sync(self, path: str, token: str = None) -> Optional[Dict]:
        t = token or self.get_token()
        base_url = f"{self._base_url()}{path}"
        headers = self.auth_headers(t)
        client = self._get_sync_client()
        try:
            resp = client.get(base_url, headers=headers)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError):
            return None

    async def _github_post(self, path: str, body: Dict = None, token: str = None) -> Optional[Dict]:
        t = token or self.get_token()
        base_url = f"{self._base_url()}{path}"
        headers = self.auth_headers(t)
        client = await self._get_async_client()
        try:
            resp = await client.post(base_url, json=body or {}, headers=headers)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError):
            return None

    async def fetch_mr(self, mr_url: str) -> Optional[Dict]:
        repo_path, pr_number = self.parse_mr_url(mr_url)
        if not repo_path or not pr_number:
            return None
        path = f"/repos/{repo_path}/pulls/{pr_number}"
        return await self._github_get_single(path)

    async def fetch_mr_comments(self, project_path: str, mr_iid: str) -> Optional[List[Dict]]:
        issue_comments = await self._github_get(f"/repos/{project_path}/issues/{mr_iid}/comments")
        review_comments = await self._github_get(f"/repos/{project_path}/pulls/{mr_iid}/comments")
        combined = []
        if issue_comments:
            combined.extend(issue_comments)
        if review_comments:
            combined.extend(review_comments)
        return combined if combined else None

    async def search_open_mrs(self, project_path: str, source_branch: str) -> Optional[List[Dict]]:
        path = f"/repos/{project_path}/pulls"
        return await self._github_get(path, {"state": "open", "head": source_branch})

    async def create_mr(self, project_path: str, source_branch: str, target_branch: str, title: str, description: str = "") -> Optional[Dict]:
        path = f"/repos/{project_path}/pulls"
        body = {
            "title": title,
            "head": source_branch,
            "base": target_branch,
            "body": description,
        }
        return await self._github_post(path, body)

    async def fetch_mr_approvals(self, project_path: str, mr_iid: str) -> Optional[Dict]:
        path = f"/repos/{project_path}/pulls/{mr_iid}/reviews"
        reviews = await self._github_get(path)
        if not reviews:
            return None
        approved = [r for r in reviews if r.get("state") == "APPROVED"]
        return {
            "approved": len(approved) > 0,
            "approvals": approved,
            "total_reviews": len(reviews),
        }

    async def list_branches(self, project_path: str) -> Optional[List[Dict]]:
        path = f"/repos/{project_path}/branches"
        return await self._github_get(path)

    async def list_projects(self, **kwargs) -> Optional[List[Dict]]:
        path = "/user/repos"
        params = {"affiliation": "owner,collaborator,organization_member"}
        params.update(kwargs)
        return await self._github_get(path, params)