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
Central configuration – environment variables and constants.
"""

import os

ORCHESTRATOR_CONFIG = os.getenv("ORCHESTRATOR_CONFIG", "/app/config/orchestrator_config.json")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", os.getenv("OPENCODE_MODEL", "llama3.1:8b"))
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))
OPENCODE_MODEL = os.getenv("OPENCODE_MODEL", OLLAMA_MODEL)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", f"{OLLAMA_HOST}/v1" if OLLAMA_HOST else "http://localhost:11434/v1")
AGENT_NAMESPACE = os.getenv("AGENT_NAMESPACE", "hivemind")
AGENT_IMAGE = os.getenv("AGENT_IMAGE", "hivemind-opencode:latest")
GITLAB_HOST = os.getenv("GITLAB_HOST") or ""
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN") or os.getenv("GIT_TOKEN") or ""
OPENCODE_PORT = os.getenv("OPENCODE_PORT", "4096")
OLLAMA_CLOUD_API_KEY = os.getenv("OLLAMA_CLOUD_API_KEY", "")

# LLM provider: ollama, ollama_cloud, openai, anthropic
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

HIVEMIND_API_KEY = os.getenv("HIVEMIND_API_KEY", "")
GITLAB_WEBHOOK_SECRET = os.getenv("GITLAB_WEBHOOK_SECRET", "")
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
AGENT_RETRY_DELAY = int(os.getenv("AGENT_RETRY_DELAY", "120"))
AGENT_MAX_RETRIES = int(os.getenv("AGENT_MAX_RETRIES", "3"))
AGENT_STALE_TIMEOUT = int(os.getenv("AGENT_STALE_TIMEOUT", "3600"))

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:8080")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_ENABLED = os.getenv("REDIS_ENABLED", "true").lower() in ("true", "1", "yes")
REDIS_LOCK_TIMEOUT = int(os.getenv("REDIS_LOCK_TIMEOUT", "30"))
REDIS_SETTINGS_TTL = int(os.getenv("REDIS_SETTINGS_TTL", "60"))

__all__ = [
    "ORCHESTRATOR_CONFIG", "OLLAMA_HOST", "OLLAMA_MODEL", "OLLAMA_TIMEOUT",
    "OPENCODE_MODEL", "OLLAMA_BASE_URL", "AGENT_NAMESPACE", "AGENT_IMAGE",
    "GITLAB_HOST", "GITLAB_TOKEN", "OPENCODE_PORT", "OLLAMA_CLOUD_API_KEY",
    "HIVEMIND_API_KEY", "GITLAB_WEBHOOK_SECRET", "RATE_LIMIT_PER_MINUTE",
    "AGENT_RETRY_DELAY", "AGENT_MAX_RETRIES", "AGENT_STALE_TIMEOUT",
    "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_ANON_KEY", "USE_SUPABASE",
    "CORS_ORIGINS", "LLM_PROVIDER", "OPENAI_API_KEY", "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY", "REDIS_URL", "REDIS_ENABLED", "REDIS_LOCK_TIMEOUT",
    "REDIS_SETTINGS_TTL",
]