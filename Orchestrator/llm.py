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
Unified LLM client – supports Ollama (local), Ollama Cloud, OpenAI, and Anthropic.

Provider is selected via LLM_PROVIDER env var:
  - "ollama"       → local Ollama server (default)
  - "ollama_cloud" → Ollama Cloud API (api.ollama.com)
  - "openai"       → OpenAI-compatible API
  - "anthropic"    → Anthropic API

All providers use the OpenAI-compatible /v1/chat/completions endpoint except
Anthropic which uses its own Messages API.
"""

import json
import logging
import os
import re
import time
from typing import Dict, List, Optional, TYPE_CHECKING

from database import get_setting
from config import OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_TIMEOUT

if TYPE_CHECKING:
    from git_manager import Ticket, RepoStatus, LeanKGManager

_struct_log = logging.getLogger("hivemind.llm")

PROVIDER_OLLAMA = "ollama"
PROVIDER_OLLAMA_CLOUD = "ollama_cloud"
PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"

PROVIDERS = {PROVIDER_OLLAMA, PROVIDER_OLLAMA_CLOUD, PROVIDER_OPENAI, PROVIDER_ANTHROPIC}

DEFAULT_BASE_URLS = {
    PROVIDER_OLLAMA: "http://localhost:11434/v1",
    PROVIDER_OLLAMA_CLOUD: "https://api.ollama.com/v1",
    PROVIDER_OPENAI: "https://api.openai.com/v1",
    PROVIDER_ANTHROPIC: "https://api.anthropic.com",
}


def _detect_provider() -> str:
    api_key = os.getenv("OLLAMA_CLOUD_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()

    if provider in PROVIDERS:
        return provider

    if openai_key:
        return PROVIDER_OPENAI
    if anthropic_key:
        return PROVIDER_ANTHROPIC
    if api_key:
        return PROVIDER_OLLAMA_CLOUD

    base_url = os.getenv("OLLAMA_BASE_URL", "")
    if "ollama.com" in base_url or "api.ollama" in base_url:
        return PROVIDER_OLLAMA_CLOUD
    if "openai.com" in base_url:
        return PROVIDER_OPENAI

    return PROVIDER_OLLAMA


def _get_base_url(provider: str) -> str:
    env_url = os.getenv("OLLAMA_BASE_URL", "").strip()
    if env_url:
        return env_url.rstrip("/")
    return DEFAULT_BASE_URLS.get(provider, DEFAULT_BASE_URLS[PROVIDER_OLLAMA])


def _get_api_key(provider: str) -> str:
    if provider == PROVIDER_OLLAMA_CLOUD:
        return os.getenv("OLLAMA_CLOUD_API_KEY", "")
    if provider == PROVIDER_OPENAI:
        return os.getenv("OPENAI_API_KEY", "")
    if provider == PROVIDER_ANTHROPIC:
        return os.getenv("ANTHROPIC_API_KEY", "")
    return os.getenv("OLLAMA_CLOUD_API_KEY", "")


def _get_default_model(provider: str) -> str:
    env_model = os.getenv("OLLAMA_MODEL", "").strip() or os.getenv("OPENCODE_MODEL", "").strip()
    if env_model:
        return env_model
    defaults = {
        PROVIDER_OLLAMA: "llama3.1:8b",
        PROVIDER_OLLAMA_CLOUD: "llama3.1:8b",
        PROVIDER_OPENAI: "gpt-4o-mini",
        PROVIDER_ANTHROPIC: "claude-sonnet-4-20250514",
    }
    return defaults.get(provider, "llama3.1:8b")


class LLMClient:
    MAX_RETRIES = 3
    RETRY_DELAYS = [5, 15, 30]

    def __init__(self, provider: str = None, base_url: str = None,
                 model: str = None, api_key: str = None, timeout: int = None):
        self.provider = provider or _detect_provider()
        self.base_url = base_url or _get_base_url(self.provider)
        self.model = model or _get_default_model(self.provider)
        self.api_key = api_key if api_key is not None else _get_api_key(self.provider)
        self.timeout = timeout or int(os.getenv("OLLAMA_TIMEOUT", "120"))

        _struct_log.info(f"LLM client initialized: provider={self.provider}, "
                         f"base_url={self.base_url}, model={self.model}")

    def _build_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            if self.provider == PROVIDER_ANTHROPIC:
                headers["x-api-key"] = self.api_key
                headers["anthropic-version"] = "2023-06-01"
            else:
                headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _post_openai(self, payload: dict) -> dict:
        import httpx
        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                body = json.dumps(payload).encode("utf-8")
                headers = self._build_headers()
                url = f"{self.base_url.rstrip('/')}/chat/completions"
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(url, content=body, headers=headers)
                    resp.raise_for_status()
                    return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (503, 429, 502) and attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt]
                    _struct_log.warning(f"LLM HTTP {e.response.status_code} (attempt {attempt+1}/{self.MAX_RETRIES}), retry in {delay}s")
                    time.sleep(delay)
                    last_error = RuntimeError(f"LLM HTTP {e.response.status_code}: {e.response.text}")
                    continue
                raise RuntimeError(f"LLM HTTP {e.response.status_code}: {e.response.text}") from e
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt]
                    _struct_log.warning(f"LLM not reachable (attempt {attempt+1}/{self.MAX_RETRIES}), retry in {delay}s")
                    time.sleep(delay)
                    last_error = e
                    continue
                raise RuntimeError(f"LLM not reachable after {self.MAX_RETRIES} attempts: {e}") from e
        raise last_error or RuntimeError("LLM request failed")

    def _post_anthropic(self, payload: dict) -> dict:
        import httpx
        messages = payload.get("messages", [])
        system_content = ""
        filtered = []
        for msg in messages:
            if msg["role"] == "system":
                system_content += msg["content"] + "\n"
            else:
                filtered.append({"role": msg["role"], "content": msg["content"]})

        anthropic_payload = {
            "model": self.model,
            "max_tokens": payload.get("max_tokens", 4096),
            "messages": filtered,
        }
        if system_content.strip():
            anthropic_payload["system"] = system_content.strip()

        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                body = json.dumps(anthropic_payload).encode("utf-8")
                headers = self._build_headers()
                url = f"{self.base_url.rstrip('/')}/v1/messages"
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(url, content=body, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                content_blocks = data.get("content", [])
                text = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
                usage = data.get("usage", {})
                return {
                    "choices": [{"message": {"content": text}}],
                    "usage": {
                        "prompt_tokens": usage.get("input_tokens", 0),
                        "completion_tokens": usage.get("output_tokens", 0),
                    },
                }
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (503, 429, 502, 529) and attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt]
                    _struct_log.warning(f"Anthropic HTTP {e.response.status_code} (attempt {attempt+1}/{self.MAX_RETRIES}), retry in {delay}s")
                    time.sleep(delay)
                    last_error = RuntimeError(f"Anthropic HTTP {e.response.status_code}: {e.response.text}")
                    continue
                raise RuntimeError(f"Anthropic HTTP {e.response.status_code}: {e.response.text}") from e
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt]
                    time.sleep(delay)
                    last_error = e
                    continue
                raise RuntimeError(f"Anthropic not reachable after {self.MAX_RETRIES} attempts: {e}") from e
        raise last_error or RuntimeError("Anthropic request failed")

    def _post(self, payload: dict) -> dict:
        if self.provider == PROVIDER_ANTHROPIC:
            return self._post_anthropic(payload)
        return self._post_openai(payload)

    def is_available(self) -> bool:
        import httpx
        try:
            headers = self._build_headers()
            if self.provider == PROVIDER_ANTHROPIC:
                url = f"{self.base_url.rstrip('/')}/v1/messages"
                with httpx.Client(timeout=5) as client:
                    resp = client.post(url, content=json.dumps({
                        "model": self.model, "max_tokens": 1,
                        "messages": [{"role": "user", "content": "hi"}],
                    }), headers=headers)
                    return resp.status_code in (200, 400)
            else:
                url = f"{self.base_url.rstrip('/')}/models"
                with httpx.Client(timeout=5) as client:
                    resp = client.get(url, headers=headers if self.api_key else {})
                    return resp.status_code == 200
        except Exception:
            return False

    def select_model_for_complexity(self, analysis: Dict) -> str:
        complexity = analysis.get("complexity", "Medium")
        routing_enabled = get_setting("model_routing_enabled") or os.getenv("MODEL_ROUTING_ENABLED", "false")
        if str(routing_enabled).lower() != "true":
            return self.model
        simple_model = get_setting("simple_model") or os.getenv("SIMPLE_MODEL", self.model)
        complex_model = get_setting("complex_model") or os.getenv("COMPLEX_MODEL", self.model)
        if complexity == "Low" and simple_model:
            _struct_log.info(f"Model routing: {complexity} complexity → {simple_model}")
            return simple_model
        elif complexity == "High" and complex_model:
            _struct_log.info(f"Model routing: {complexity} complexity → {complex_model}")
            return complex_model
        return self.model

    def analyze_repos_for_ticket(self, ticket: "Ticket", repo_contexts: List["RepoStatus"], leankg: "LeanKGManager") -> Dict:
        prompt = self._make_prompt(ticket, repo_contexts, leankg)
        model = self.select_model_for_complexity({"complexity": "Medium"})
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": (
                    "You are a Senior Architect. Analyze the ticket and select the repositories "
                    "most likely affected. Respond ONLY as JSON."
                )},
                {"role": "user", "content": prompt}
            ],
            "stream": False,
        }
        if self.provider == PROVIDER_OLLAMA:
            body["format"] = "json"

        raw = self._post(body)

        usage = raw.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        content = raw.get("message", {}).get("content", "")
        if not content:
            choices = raw.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                if not usage and choices[0].get("usage"):
                    usage = choices[0]["usage"]
                    prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                    completion_tokens = usage.get("completion_tokens", completion_tokens)
        if not content:
            raise RuntimeError(f"LLM returned no answer: {raw}")
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*\n?", "", content)
            content = re.sub(r"\n?\s*```\s*$", "", content)
            content = content.strip()
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r'\{.*\}', content, re.DOTALL)
            if m:
                result = json.loads(m.group(0))
            else:
                raise RuntimeError(f"No JSON in LLM response: {content[:300]}")

        if prompt_tokens or completion_tokens:
            result["_llm_usage"] = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "model": model,
                "provider": self.provider,
            }
        return result

    def _make_prompt(self, ticket: "Ticket", repo_contexts: List["RepoStatus"], leankg: "LeanKGManager") -> str:
        blocks = []
        for ctx in repo_contexts:
            keywords = (ticket.title + " " + ticket.description).lower().split()[:20]
            leankg_files = []
            if ctx.leankg_ready:
                leankg_files = [r["summary"] for r in leankg.check_repo_context(ctx.local_path, " ".join(keywords))]
            blocks.append({
                "name": ctx.config.name,
                "description": ctx.config.description or "No description",
                "tags": ctx.config.tags,
                "available": not bool(ctx.error),
                "leankg_context": {"indexed": ctx.leankg_ready, "recent_files": leankg_files[:5]},
            })
        if not blocks:
            _struct_log.warning("No repo contexts available for LLM prompt")

        return json.dumps({
            "instruction": ("Select 1–4 repositories for this ticket. "
                            "Return JSON with: selected_repos[], primary_repo, "
                            "complexity (Low/Medium/High), estimated_hours, reasoning."),
            "ticket": {
                "id": ticket.id,
                "title": ticket.title,
                "description": ticket.description,
                "labels": ticket.labels,
            },
            "repositories": blocks,
        }, ensure_ascii=False, indent=2)


# Backward-compatible alias
OllamaClient = LLMClient


__all__ = ["LLMClient", "OllamaClient", "PROVIDER_OLLAMA", "PROVIDER_OLLAMA_CLOUD",
           "PROVIDER_OPENAI", "PROVIDER_ANTHROPIC"]