"""
Ollama / OpenAI-compatible LLM client.
"""

import json
import logging
import os
import re
from typing import Dict, List, TYPE_CHECKING

from database import get_setting
from config import OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_TIMEOUT

if TYPE_CHECKING:
    from git_manager import Ticket, RepoStatus, LeanKGManager

_struct_log = logging.getLogger("hivemind.main")


class OllamaClient:
    MAX_RETRIES = 3
    RETRY_DELAYS = [5, 15, 30]

    def __init__(self, host: str = OLLAMA_HOST, model: str = OLLAMA_MODEL, timeout: int = OLLAMA_TIMEOUT):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout

    def _post(self, payload: dict) -> dict:
        import httpx
        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                body = json.dumps(payload).encode("utf-8")
                headers = {"Content-Type": "application/json"}
                api_key = os.getenv("OLLAMA_CLOUD_API_KEY", "")
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                base_url = os.getenv("OLLAMA_BASE_URL", self.host + "/v1")
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(f"{base_url.rstrip('/')}/chat/completions", content=body, headers=headers)
                    resp.raise_for_status()
                    return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (503, 429, 502) and attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt]
                    _struct_log.warning(f"Ollama HTTP {e.response.status_code} (attempt {attempt+1}/{self.MAX_RETRIES}), retry in {delay}s")
                    import time
                    time.sleep(delay)
                    last_error = RuntimeError(f"Ollama HTTP {e.response.status_code}: {e.response.text}")
                    continue
                raise RuntimeError(f"Ollama HTTP {e.response.status_code}: {e.response.text}") from e
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt]
                    _struct_log.warning(f"Ollama not reachable (attempt {attempt+1}/{self.MAX_RETRIES}), retry in {delay}s")
                    import time
                    time.sleep(delay)
                    last_error = e
                    continue
                raise RuntimeError(f"Ollama not reachable after {self.MAX_RETRIES} attempts: {e}") from e
        raise last_error or RuntimeError("Ollama failed")

    def is_available(self) -> bool:
        import httpx
        try:
            base_url = os.getenv("OLLAMA_BASE_URL", self.host + "/v1")
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{base_url.rstrip('/')}/models")
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
        if complexity in ("Low",) and simple_model:
            _struct_log.info(f"Model routing: {complexity} complexity → {simple_model}")
            return simple_model
        elif complexity in ("High",) and complex_model:
            _struct_log.info(f"Model routing: {complexity} complexity → {complex_model}")
            return complex_model
        return self.model

    def analyze_repos_for_ticket(self, ticket: "Ticket", repo_contexts: List["RepoStatus"], leankg: "LeanKGManager") -> Dict:
        prompt = self._make_prompt(ticket, repo_contexts, leankg)
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": (
                    "You are a Senior Architect. Analyze the ticket and select the repositories "
                    "most likely affected. Respond ONLY as JSON."
                )},
                {"role": "user", "content": prompt}
            ],
            "stream": False,
            "format": "json"
        }
        raw = self._post(body)
        content = raw.get("message", {}).get("content", "")
        if not content:
            choices = raw.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
        if not content:
            raise RuntimeError(f"Ollama returned no answer: {raw}")
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*\n?", "", content)
            content = re.sub(r"\n?\s*```\s*$", "", content)
            content = content.strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r'\{.*\}', content, re.DOTALL)
            if m:
                return json.loads(m.group(0))
            raise RuntimeError(f"No JSON in Ollama response: {content[:300]}")

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


__all__ = ["OllamaClient"]