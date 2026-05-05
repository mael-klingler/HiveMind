#!/usr/bin/env python3
"""
Async HTTP client for GitLab API calls – replaces urllib.request with httpx.
"""

import json
import logging
import os
from typing import Dict, List, Optional

import httpx

log = logging.getLogger("hivemind.gitlab")

_gitlab_client: Optional[httpx.AsyncClient] = None
_sync_client: Optional[httpx.Client] = None


def get_gitlab_token() -> str:
    return os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))


def get_gitlab_host() -> str:
    return os.getenv("GITLAB_HOST", os.getenv("GIT_HOST", ""))


def _gitlab_headers(token: str = None) -> Dict[str, str]:
    t = token or get_gitlab_token()
    headers = {"Content-Type": "application/json"}
    if t:
        headers["PRIVATE-TOKEN"] = t
    return headers


async def get_async_client() -> httpx.AsyncClient:
    global _gitlab_client
    if _gitlab_client is None or _gitlab_client.is_closed:
        _gitlab_client = httpx.AsyncClient(timeout=30.0)
    return _gitlab_client


async def close_async_client():
    global _gitlab_client
    if _gitlab_client and not _gitlab_client.is_closed:
        await _gitlab_client.aclose()
        _gitlab_client = None


def get_sync_client() -> httpx.Client:
    global _sync_client
    if _sync_client is None or _sync_client.is_closed:
        _sync_client = httpx.Client(timeout=30.0)
    return _sync_client


def close_sync_client():
    global _sync_client
    if _sync_client and not _sync_client.is_closed:
        _sync_client.close()
        _sync_client = None


async def gitlab_get(path: str, params: Dict = None, gitlab_host: str = None, gitlab_token: str = None) -> Optional[List[Dict]]:
    gitlab_host = gitlab_host or get_gitlab_host()
    gitlab_token = gitlab_token or get_gitlab_token()
    if not gitlab_host or not gitlab_token:
        return None

    client = await get_async_client()
    base_url = f"https://{gitlab_host}/api/v4{path}"
    headers = _gitlab_headers(gitlab_token)
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


async def gitlab_get_single(path: str, gitlab_host: str = None, gitlab_token: str = None) -> Optional[Dict]:
    gitlab_host = gitlab_host or get_gitlab_host()
    gitlab_token = gitlab_token or get_gitlab_token()
    if not gitlab_host or not gitlab_token:
        return None

    client = await get_async_client()
    url = f"https://{gitlab_host}/api/v4{path}"
    headers = _gitlab_headers(gitlab_token)
    try:
        resp = await client.get(url, headers=headers)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPStatusError, httpx.RequestError):
        return None


def gitlab_get_sync(path: str, params: Dict = None, gitlab_host: str = None, gitlab_token: str = None) -> Optional[List[Dict]]:
    gitlab_host = gitlab_host or get_gitlab_host()
    gitlab_token = gitlab_token or get_gitlab_token()
    if not gitlab_host or not gitlab_token:
        return None

    client = get_sync_client()
    base_url = f"https://{gitlab_host}/api/v4{path}"
    headers = _gitlab_headers(gitlab_token)
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


def gitlab_get_single_sync(path: str, gitlab_host: str = None, gitlab_token: str = None) -> Optional[Dict]:
    gitlab_host = gitlab_host or get_gitlab_host()
    gitlab_token = gitlab_token or get_gitlab_token()
    if not gitlab_host or not gitlab_token:
        return None

    client = get_sync_client()
    url = f"https://{gitlab_host}/api/v4{path}"
    headers = _gitlab_headers(gitlab_token)
    try:
        resp = client.get(url, headers=headers)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPStatusError, httpx.RequestError):
        return None


async def fetch_mr(gitlab_host: str, gitlab_token: str, mr_url: str) -> Optional[Dict]:
    project_path, mr_iid = parse_mr_url(mr_url)
    if not project_path or not mr_iid:
        return None
    encoded_path = project_path.replace("/", "%2F")
    path = f"/projects/{encoded_path}/merge_requests/{mr_iid}"
    return await gitlab_get_single(path, gitlab_host, gitlab_token)


def fetch_mr_sync(gitlab_host: str, gitlab_token: str, mr_url: str) -> Optional[Dict]:
    project_path, mr_iid = parse_mr_url(mr_url)
    if not project_path or not mr_iid:
        return None
    encoded_path = project_path.replace("/", "%2F")
    path = f"/projects/{encoded_path}/merge_requests/{mr_iid}"
    return gitlab_get_single_sync(path, gitlab_host, gitlab_token)


async def fetch_mr_comments(gitlab_host: str, gitlab_token: str, project_path: str, mr_iid: str) -> Optional[List[Dict]]:
    encoded_path = project_path.replace("/", "%2F")
    path = f"/projects/{encoded_path}/merge_requests/{mr_iid}/notes"
    return await gitlab_get(path, {"sort": "asc", "per_page": "50"}, gitlab_host, gitlab_token)


async def search_open_mrs(gitlab_host: str, gitlab_token: str, project_path: str, source_branch: str) -> Optional[List[Dict]]:
    encoded_path = project_path.replace("/", "%2F")
    path = f"/projects/{encoded_path}/merge_requests"
    return await gitlab_get(path, {"state": "opened", "source_branch": source_branch}, gitlab_host, gitlab_token)


def parse_mr_url(mr_url: str) -> tuple:
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