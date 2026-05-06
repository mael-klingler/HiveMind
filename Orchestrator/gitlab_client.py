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
Backward-compatible GitLab client – delegates to vcs.gitlab.GitLabProvider.

All existing function signatures are preserved so that imports from
gitlab_client continue to work without changes.
"""

from typing import Dict, List, Optional

from vcs.gitlab import GitLabProvider

_provider = GitLabProvider()


def get_gitlab_token() -> str:
    return _provider.get_token()


def get_gitlab_host() -> str:
    return _provider.get_host()


def _gitlab_headers(token: str = None) -> Dict[str, str]:
    return _provider.auth_headers(token)


async def get_async_client():
    return await _provider._get_async_client()


async def close_async_client():
    await _provider.close_async_client()


def get_sync_client():
    return _provider._get_sync_client()


def close_sync_client():
    _provider.close_sync_client()


async def gitlab_get(path: str, params: Dict = None, gitlab_host: str = None, gitlab_token: str = None) -> Optional[List[Dict]]:
    return await _provider._gitlab_get(path, params, gitlab_host, gitlab_token)


async def gitlab_get_single(path: str, gitlab_host: str = None, gitlab_token: str = None) -> Optional[Dict]:
    return await _provider._gitlab_get_single(path, gitlab_host, gitlab_token)


def gitlab_get_sync(path: str, params: Dict = None, gitlab_host: str = None, gitlab_token: str = None) -> Optional[List[Dict]]:
    return _provider._gitlab_get_sync(path, params, gitlab_host, gitlab_token)


def gitlab_get_single_sync(path: str, gitlab_host: str = None, gitlab_token: str = None) -> Optional[Dict]:
    return _provider._gitlab_get_single_sync(path, gitlab_host, gitlab_token)


async def fetch_mr(gitlab_host: str, gitlab_token: str, mr_url: str) -> Optional[Dict]:
    return await _provider._gitlab_get_single(
        _build_mr_path(mr_url), gitlab_host, gitlab_token
    )


def fetch_mr_sync(gitlab_host: str, gitlab_token: str, mr_url: str) -> Optional[Dict]:
    return _provider._gitlab_get_single_sync(
        _build_mr_path(mr_url), gitlab_host, gitlab_token
    )


async def fetch_mr_comments(gitlab_host: str, gitlab_token: str, project_path: str, mr_iid: str) -> Optional[List[Dict]]:
    encoded_path = project_path.replace("/", "%2F")
    path = f"/projects/{encoded_path}/merge_requests/{mr_iid}/notes"
    return await _provider._gitlab_get(path, {"sort": "asc", "per_page": "50"}, gitlab_host, gitlab_token)


async def search_open_mrs(gitlab_host: str, gitlab_token: str, project_path: str, source_branch: str) -> Optional[List[Dict]]:
    encoded_path = project_path.replace("/", "%2F")
    path = f"/projects/{encoded_path}/merge_requests"
    return await _provider._gitlab_get(path, {"state": "opened", "source_branch": source_branch}, gitlab_host, gitlab_token)


def parse_mr_url(mr_url: str) -> tuple:
    return _provider.parse_mr_url(mr_url)


def _build_mr_path(mr_url: str) -> Optional[str]:
    project_path, mr_iid = _provider.parse_mr_url(mr_url)
    if not project_path or not mr_iid:
        return None
    encoded_path = project_path.replace("/", "%2F")
    return f"/projects/{encoded_path}/merge_requests/{mr_iid}"