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

#!/usr/bin/env python3
"""
VCS Provider abstraction – interface for version control system operations.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple


class VCSProvider(ABC):
    """Base interface for VCS provider operations."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def token_env_key(self) -> str:
        ...

    @property
    @abstractmethod
    def host_env_key(self) -> str:
        ...

    @abstractmethod
    def get_token(self) -> str:
        ...

    @abstractmethod
    def get_host(self) -> str:
        ...

    @abstractmethod
    def auth_headers(self, token: str = None) -> Dict[str, str]:
        ...

    @abstractmethod
    def parse_mr_url(self, mr_url: str) -> Tuple[Optional[str], Optional[str]]:
        ...

    @abstractmethod
    async def fetch_mr(self, mr_url: str) -> Optional[Dict]:
        ...

    @abstractmethod
    async def fetch_mr_comments(self, project_path: str, mr_iid: str) -> Optional[List[Dict]]:
        ...

    @abstractmethod
    async def search_open_mrs(self, project_path: str, source_branch: str) -> Optional[List[Dict]]:
        ...

    @abstractmethod
    async def create_mr(self, project_path: str, source_branch: str, target_branch: str, title: str, description: str = "") -> Optional[Dict]:
        ...

    @abstractmethod
    async def fetch_mr_approvals(self, project_path: str, mr_iid: str) -> Optional[Dict]:
        ...

    @abstractmethod
    def parse_webhook_event(self, payload: Dict, headers: Dict) -> Optional[Dict]:
        ...

    @abstractmethod
    def get_branch_list_url(self, project_path: str) -> str:
        ...

    @abstractmethod
    def get_default_git_user(self) -> str:
        ...

    @abstractmethod
    def extract_ticket_id_from_branch(self, branch: str) -> Optional[str]:
        ...

    @abstractmethod
    async def list_branches(self, project_path: str) -> Optional[List[Dict]]:
        ...

    @abstractmethod
    async def list_projects(self, **kwargs) -> Optional[List[Dict]]:
        ...