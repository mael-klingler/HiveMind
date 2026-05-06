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

import os
from typing import Dict, List, Optional

from database.sqlite_backend import get_db


def ensure_config_defaults():
    defaults = {
        "max_agents": os.getenv("MAX_AGENTS", "3"),
        "polling_interval_seconds": os.getenv("POLLING_INTERVAL_SECONDS", "5"),
        "git_host": os.getenv("GITLAB_HOST") or "",
        "git_user": os.getenv("GIT_USER", "gitlab-ci-token"),
        "git_token": os.getenv("GITLAB_TOKEN", ""),
        "ollama_host": os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        "ollama_model": os.getenv("OLLAMA_MODEL", "glm-5.1:cloud"),
        "opencode_model": os.getenv("OPENCODE_MODEL", "glm-5.1:cloud"),
        "auto_pull_enabled": "true",
        "default_branch": "development",
        "branch_fallback_order": "development,qa,main",
    }
    conn = get_db()
    c = conn.cursor()
    for key, value in defaults.items():
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def get_all_settings() -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT key, value FROM config ORDER BY key")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_setting(key: str) -> Optional[str]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT value FROM config WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row["value"] if row else None


def set_setting(key: str, value: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def import_settings_from_env():
    """Overrides settings from environment variables."""
    env_mapping = {
        "GIT_HOST": "git_host",
        "GIT_USER": "git_user",
        "GIT_TOKEN": "git_token",
        "OLLAMA_HOST": "ollama_host",
        "OLLAMA_MODEL": "ollama_model",
        "TRACK_BRANCH": "default_branch",
        "BRANCH_FALLBACK_ORDER": "branch_fallback_order",
    }
    for env_key, db_key in env_mapping.items():
        val = os.getenv(env_key)
        if val:
            set_setting(db_key, val)