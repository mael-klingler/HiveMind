#!/usr/bin/env python3
"""
Orchestrator – K8s/PVC-ready Workspace Generator

Laedt .env aus /app/config/.env oder lokaler .env
und konfiguriert Git-Token-Auth automatisch.
"""

import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from database import set_ticket_ai_planning, get_enabled_mcp_servers, get_enabled_agent_instructions, get_agent_mcp_servers, get_agent_assigned_instructions, get_enabled_plugin_names, get_agent_memory_as_markdown, get_setting

# ── .env Loader ────────────────────────────────────────────────────

def _load_dotenv(path: str = "/app/config/.env"):
    """Laedt Key=Value Paare aus einer .env Datei in os.environ."""
    p = Path(path)
    if not p.exists():
        p = Path(".env")
        if not p.exists():
            return
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                key = k.strip()
                value = v.strip().strip('\'"')
                if key not in os.environ:
                    os.environ[key] = value

_load_dotenv()


# ── Environment Konfiguration (zentral, Defaults fuer lokale Entwicklung) ──

ORCHESTRATOR_CONFIG = os.getenv("ORCHESTRATOR_CONFIG", "/app/config/orchestrator_config.json")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "glm-5.1:cloud")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))
OPENCODE_MODEL = os.getenv("OPENCODE_MODEL", "glm-5.1:cloud")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
AGENT_NAMESPACE = os.getenv("AGENT_NAMESPACE", "hivemind")
AGENT_IMAGE = os.getenv("AGENT_IMAGE", "hivemind-opencode:latest")
GITLAB_HOST = os.getenv("GITLAB_HOST", "gitlab.example.com")
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))
OPENCODE_PORT = os.getenv("OPENCODE_PORT", "4096")
OLLAMA_CLOUD_API_KEY = os.getenv("OLLAMA_CLOUD_API_KEY", "")


# ── Datenklassen ─────────────────────────────────────────────────

@dataclass
class RepoConfig:
    name: str
    url: str
    branch: str
    description: str
    tags: List[str]

    @classmethod
    def from_dict(cls, data: dict) -> "RepoConfig":
        return cls(
            name=data["name"],
            url=data["url"],
            branch=data.get("branch", "main"),
            description=data.get("description", ""),
            tags=data.get("tags", []),
        )


@dataclass
class OrchestratorConfig:
    work_dir: str
    pvc_mount_path: str
    track_branch: str
    auto_pull_interval_minutes: int
    leankg_enabled: bool
    ollama_host: str
    ollama_model: str
    max_related_files_per_repo: int
    log_level: str
    repositories: List[RepoConfig]

    @classmethod
    def from_file(cls, path: str) -> "OrchestratorConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        from database import get_all_repos, import_repos_from_config
        import_repos_from_config(path)

        db_repos = get_all_repos()
        repositories = [RepoConfig.from_dict(r) for r in db_repos]

        return cls(
            work_dir=data.get("work_dir", "/app/workspace"),
            pvc_mount_path=data.get("pvc_mount_path", "/app/workspace/repos"),
            track_branch=data.get("track_branch", "main"),
            auto_pull_interval_minutes=data.get("auto_pull_interval_minutes", 60),
            leankg_enabled=data.get("leankg_enabled", True),
            ollama_host=os.getenv("OLLAMA_HOST", data.get("ollama_host", "http://localhost:11434")),
            ollama_model=os.getenv("OLLAMA_MODEL", data.get("ollama_model", "glm-5.1:cloud")),
            max_related_files_per_repo=data.get("max_related_files_per_repo", 5),
            log_level=data.get("log_level", "INFO"),
            repositories=repositories,
        )


@dataclass
class RepoStatus:
    config: RepoConfig
    local_path: Path
    exists: bool = False
    changes_detected: bool = False
    latest_commit: str = ""
    error: str = ""
    leankg_ready: bool = False


@dataclass
class Ticket:
    id: str
    title: str = ""
    description: str = ""
    labels: List[str] = field(default_factory=list)
    issue_type: str = "Task"
    priority: str = "Medium"
    agent_id: str = ""

    @classmethod
    def from_json(cls, path: str) -> "Ticket":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            id=str(data.get("id", data.get("key", "UNKNOWN"))),
            title=data.get("title", data.get("summary", "")),
            description=data.get("description", ""),
            labels=data.get("labels", []),
            issue_type=data.get("issue_type", data.get("type", "Task")),
            priority=data.get("priority", "Medium"),
        )


# ── Logger ─────────────────────────────────────────────────────────

class Logger:
    def info(self, msg: str):
        print(f"   ℹ️  {msg}")

    def ok(self, msg: str):
        print(f"   ✅ {msg}")

    def warn(self, msg: str):
        print(f"   ⚠️  {msg}")

    def error(self, msg: str):
        print(f"   ❌ {msg}")

    def step(self, msg: str):
        print(f"\n📌 {msg}")

    def sub(self, msg: str):
        print(f"   🔹 {msg}")

log = Logger()


# ── Git Manager ────────────────────────────────────────────────────

class RepoManager:
    def __init__(self, base_dir: str, default_branch: str):
        self.base_dir = Path(base_dir)
        self.default_branch = default_branch

    def _run(self, *cmd, cwd: Optional[str] = None) -> Tuple[int, str, str]:
        try:
            result = subprocess.run(list(cmd), capture_output=True, text=True, cwd=cwd, timeout=120)
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return -101, "", f"Git command timed out: {' '.join(cmd)}"
        except FileNotFoundError:
            return -100, "", "git nicht gefunden"

    def ensure_repo(self, config: RepoConfig) -> RepoStatus:
        repo_dir = self.base_dir / config.name
        status = RepoStatus(config=config, local_path=repo_dir)

        branch = config.branch or self.default_branch

        if repo_dir.exists() and (repo_dir / ".git").exists():
            rc, _, _ = self._run("git", "fetch", "origin", str(branch), cwd=str(repo_dir))
            if rc != 0:
                status.error = f"fetch failed: {branch}"
                log.error(f"{config.name}: fetch failed")
                log.error(f"Stderr: {_}")
                return status

            rc, local_commit, _ = self._run("git", "rev-parse", f"heads/{branch}", cwd=str(repo_dir))
            rc2, remote_commit, _ = self._run("git", "rev-parse", f"origin/{branch}", cwd=str(repo_dir))
            status.latest_commit = remote_commit or local_commit

            if local_commit != remote_commit:
                status.changes_detected = True
                log.sub(f"{config.name}: neue commits ({local_commit[:8]} → {remote_commit[:8]})")
                rc, _, err = self._run("git", "reset", "--hard", f"origin/{branch}", cwd=str(repo_dir))
                if rc:
                    status.error = f"pull failed: {err}"
            else:
                log.sub(f"{config.name}: up-to-date ({remote_commit[:8]})")

            status.exists = True
        else:
            url = config.url
            token = os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))
            if re.match(r"^https?://", url) and token and "@" not in url.split("://")[1].split("/")[0]:
                git_user = get_setting("git_user") or os.getenv("GIT_USER", "gitlab-ci-token")
                url = re.sub(r"^(https?://)", rf"\1{git_user}:{token}@", url)
            rc, out, err = self._run("git", "clone", "--depth=100", "-b", str(branch), url, str(repo_dir))
            if rc != 0 and "not found in upstream" in err:
                fallback_branch = self.default_branch
                if fallback_branch != str(branch):
                    log.sub(f"{config.name}: branch '{branch}' nicht gefunden, versuche '{fallback_branch}'...")
                    rc, out, err = self._run("git", "clone", "--depth=100", "-b", str(fallback_branch), url, str(repo_dir))
            if rc != 0:
                status.error = f"Clone fehlgeschlagen: {err}"
                log.error(f"{config.name}: clone fehlgeschlagen")
                log.error(f"Stderr: {err}")
                return status
            rc, commit, _ = self._run("git", "rev-parse", "HEAD", cwd=str(repo_dir))
            status.latest_commit = commit
            status.exists = True
            log.sub(f"{config.name}: geklont ({commit[:8]})")

        return status

    def init_all(self, repos: List[RepoConfig]) -> List[RepoStatus]:
        log.step("Repository Initialisierung")
        return [self.ensure_repo(r) for r in repos]

    def update_all(self, repos: List[RepoConfig]) -> List[RepoStatus]:
        log.step("Repository Update")
        return [self.ensure_repo(r) for r in repos]


# ── LeanKG Manager ────────────────────────────────────────────────

class LeanKGManager:
    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self.cmd = "leankg"

    def _run(self, *args, cwd: Optional[str] = None) -> Tuple[int, str, str]:
        try:
            result = subprocess.run([self.cmd, *args], capture_output=True, text=True, cwd=cwd, timeout=60)
            return result.returncode, result.stdout, result.stderr
        except FileNotFoundError:
            return -100, "", "leankg nicht gefunden"
        except subprocess.TimeoutExpired:
            return -101, "", "Timeout"

    def is_available(self) -> bool:
        rc, _, _ = self._run("--version")
        return rc == 0

    def init_repo(self, path: Path) -> bool:
        if (path / ".leankg").exists():
            return True
        rc, _, _ = self._run("init", cwd=str(path))
        return rc == 0

    def index_repo(self, path: Path) -> bool:
        self.init_repo(path)
        rc, _, _ = self._run("index", ".", cwd=str(path))
        return rc == 0

    def check_repo_context(self, path: Path, query: str, limit: int = 5) -> List[Dict]:
        rc, out, _ = self._run("query", *query, cwd=str(path))
        if rc != 0:
            return []
        results = []
        for line in out.strip().splitlines()[:limit]:
            if line.strip():
                results.append({"summary": line.strip()})
        return results

    def index_all(self, statuses: List[RepoStatus]):
        if not self.is_available():
            log.warn("LeanKG CLI nicht verfuegbar → ueberspringe Indexierung (Repos sind trotzdem geklont)")
            return
        log.step("LeanKG Indexierung")
        for s in statuses:
            if s.error:
                continue
            log.sub(f"{s.config.name}: indexiere...")
            if self.index_repo(s.local_path):
                log.ok(f"{s.config.name}: indiziert")
                s.leankg_ready = True


# ── Ollama Client ────────────────────────────────────────────────

class OllamaClient:
    MAX_RETRIES = 3
    RETRY_DELAYS = [5, 15, 30]

    def __init__(self, host: str = OLLAMA_HOST, model: str = OLLAMA_MODEL, timeout: int = OLLAMA_TIMEOUT):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout

    def _post(self, payload: dict) -> dict:
        import urllib.error
        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                body = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(f"{self.host}/api/chat",
                                             data=body,
                                             headers={"Content-Type": "application/json"},
                                             method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                error_body = e.read().decode("utf-8", errors="replace")
                if e.code in (503, 429, 502) and attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt]
                    print(f"   ⚠️  Ollama HTTP {e.code} (Versuch {attempt+1}/{self.MAX_RETRIES}), Retry in {delay}s...")
                    import time
                    time.sleep(delay)
                    last_error = RuntimeError(f"Ollama HTTP {e.code}: {error_body}")
                    continue
                raise RuntimeError(f"Ollama HTTP {e.code}: {error_body}") from e
            except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt]
                    print(f"   ⚠️  Ollama nicht erreichbar (Versuch {attempt+1}/{self.MAX_RETRIES}), Retry in {delay}s...")
                    import time
                    time.sleep(delay)
                    last_error = e
                    continue
                raise RuntimeError(f"Ollama nicht erreichbar nach {self.MAX_RETRIES} Versuchen: {e}") from e
        raise last_error or RuntimeError("Ollama fehlgeschlagen")

    def is_available(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.host}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status == 200
        except Exception:
            return False

    def analyze_repos_for_ticket(self, ticket: Ticket, repo_contexts: List[RepoStatus], leankg: LeanKGManager) -> Dict:
        prompt = self._make_prompt(ticket, repo_contexts, leankg)
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": (
                    "Du bist ein Senior-Architekt. Analysiere das Ticket und waehle die Repositories, "
                    "die am wahrscheinlichsten betroffen sind. Antworte NUR als JSON."
                )},
                {"role": "user", "content": prompt}
            ],
            "stream": False,
            "format": "json"
        }
        raw = self._post(body)
        content = raw.get("message", {}).get("content", "")
        if not content:
            raise RuntimeError(f"Ollama lieferte keine Antwort: {raw}")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            import re
            m = re.search(r'\{.*\}', content, re.DOTALL)
            if m:
                return json.loads(m.group(0))
            raise RuntimeError(f"Kein JSON in Ollama-Antwort: {content[:300]}")

    def _make_prompt(self, ticket: Ticket, repo_contexts: List[RepoStatus], leankg: LeanKGManager) -> str:
        blocks = []
        for ctx in repo_contexts:
            if ctx.error:
                continue
            keywords = (ticket.title + " " + ticket.description).lower().split()[:20]
            leankg_files = []
            if ctx.leankg_ready:
                leankg_files = [r["summary"] for r in leankg.check_repo_context(ctx.local_path, " ".join(keywords))]
            blocks.append({
                "name": ctx.config.name,
                "description": ctx.config.description or "No description",
                "tags": ctx.config.tags,
                "leankg_context": {"indexed": ctx.leankg_ready, "recent_files": leankg_files[:5]},
            })

        return json.dumps({
            "instruction": ("Waehle 1–4 Repositories fuer dieses Ticket. "
                            "Gib JSON zurueck mit: selected_repos[], primary_repo, "
                            "complexity (Low/Medium/High), estimated_hours, reasoning."),
            "ticket": {
                "id": ticket.id,
                "title": ticket.title,
                "description": ticket.description,
                "labels": ticket.labels,
            },
            "repositories": blocks,
        }, ensure_ascii=False, indent=2)




# ── Helper: Git Credentials ──────────────────────────────────────

def configure_git_credentials():
    from database import get_all_repos, get_setting as _gs
    token = os.getenv("GITLAB_TOKEN") or os.getenv("GIT_TOKEN") or ""
    git_user = _gs("git_user") or os.getenv("GIT_USER", "gitlab-ci-token")
    hosts = set()
    default_host = os.getenv("GITLAB_HOST", "gitlab.example.com")
    if default_host:
        hosts.add(default_host)
    try:
        repos = get_all_repos()
        for r in repos:
            url = r.get("url", "") if isinstance(r, dict) else getattr(r, "url", "")
            if url and "://" in url:
                host = url.split("://")[1].split("/")[0].split(":")[0]
                hosts.add(host)
    except Exception:
        pass
    git_dir = Path.home() / ".git-credentials"
    if token and hosts:
        lines = [f"https://{git_user}:{token}@{h}\n" for h in sorted(hosts)]
        git_dir.write_text("".join(lines), encoding="utf-8")
        subprocess.run(["git", "config", "--global", "credential.helper", "store"], check=False)
        subprocess.run(["git", "config", "--global", "user.email", "hivemind-agents@example.com"], check=False)
        subprocess.run(["git", "config", "--global", "user.name", "HiveMind"], check=False)
        log.ok(f"Git-Credentials fuer {', '.join(sorted(hosts))} gesetzt")
    else:
        log.warn("GITLAB_TOKEN nicht gesetzt – ggf. Clone-Fehler")


# ── Workspace Utilities ─────────────────────────────────────────

def create_opencode_config(workspace_dir: Path, ticket: Ticket, selected: List[RepoConfig],
                           analysis: Dict, assignment_md: str):
    """Erzeugt .opencode/opencode.json fuer den Agent."""

    git_user = get_setting("git_user") or os.getenv("GIT_USER", "gitlab-ci-token")
    git_token = os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))
    repo_list = []
    for r in selected:
        remote = r.url or f"https://{os.getenv('GITLAB_HOST', 'gitlab.example.com')}/{r.name}.git"
        repo_list.append({
            "url": remote,
            "name": r.name,
            "branch": r.branch or "main",
            "primary": r.name == analysis.get("primary_repo", ""),
        })

    config = {
        "ticket": {
            "id": ticket.id,
            "title": ticket.title,
            "description": ticket.description,
            "labels": ticket.labels,
            "issue_type": ticket.issue_type,
            "priority": ticket.priority,
        },
        "analysis": analysis,
        "repositories": repo_list,
        "assignment": assignment_md,
    }

    opencode_dir = workspace_dir / ".opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)

    (opencode_dir / "config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")

    log.ok("Workspace .opencode/ erzeugt")


def create_launch_scripts(workspace_dir: Path):
    """Erzeugt start.sh und entrypoint.sh im Workspace."""
    start_sh = workspace_dir / "start.sh"
    start_sh.write_text("""#!/bin/bash
set -e
echo "🚀 Starte Agent..."
cd "$(dirname "$0")"
if [ -f .opencode/opencode.json ]; then
    echo "📋 Aufgabe geladen:"
    cat .opencode/config.json | jq -r '.ticket.title'
fi
bash "$(dirname "$0")/entrypoint.sh"
""", encoding="utf-8")

    entrypoint_sh = workspace_dir / "entrypoint.sh"
    entrypoint_sh.write_text("""#!/bin/bash
set -e
WORKSPACE="$(pwd)"
echo "📋 Ticket:  $(jq -r '.ticket.id' $WORKSPACE/.opencode/config.json) – $(jq -r '.ticket.title' $WORKSPACE/.opencode/config.json)"
echo "🌿 Branch:  feature/$(jq -r '.ticket.id' $WORKSPACE/.opencode/config.json)"
echo "🧪 Dry-Run: false"
echo "🤖 Starte opencode mit Task..."
export OPENCODE_MODEL="ollama/{OPENCODE_MODEL}"
export OLLAMA_HOST="https://ollama.com/v1"
export OLLAMA_CLOUD_API_KEY="{OLLAMA_CLOUD_API_KEY}"
cd "$WORKSPACE"
for repo in $(jq -r '.repositories[].name' $WORKSPACE/.opencode/config.json); do
    if [ -d "$repo" ]; then
        echo "📦 $repo: Bearbeite..."
        cd "$WORKSPACE/$repo"
        if ! git diff --quiet HEAD 2>/dev/null; then
            echo "✅ Änderungen erkannt, erstelle Commit..."
            git add -A
            git commit -m "Agent: $(jq -r '.ticket.title' $WORKSPACE/.opencode/config.json)"
            git push origin HEAD:feature/$(jq -r '.ticket.id' $WORKSPACE/.opencode/config.json) || true
        else
            echo "📦 $repo: Keine Änderungen, überspringe."
        fi
        cd "$WORKSPACE"
    fi
done
echo "🏁 Alle Repos verarbeitet."
""", encoding="utf-8")

    start_sh.chmod(0o755)
    entrypoint_sh.chmod(0o755)
    log.ok("Launch Scripts erzeugt")


def generate_assignment_prompt(ticket: Ticket, analysis: Dict, repos: List[RepoConfig]) -> str:
    complexity = analysis.get("complexity", "Medium")
    estimates = analysis.get("estimated_hours", "?")
    primary = analysis.get("primary_repo", "–")
    reasoning = analysis.get("reasoning", "")

    # Bei Retry: review_notes als Kontext hinzufuegen
    retry_context = ""
    review_notes = analysis.get("review_notes", "")
    mr_url = analysis.get("mr_url", "")
    pipeline_status = analysis.get("pipeline_status", "")
    retry_count = analysis.get("retry_count", 0)

    if retry_count > 0 or review_notes or pipeline_status == "failed":
        retry_context = f"""

## ⚠️ Retry-Kontext (Versuch {retry_count + 1})
Dieses Ticket wurde bereits bearbeitet, aber es gab Probleme die behoben werden muessen:

WICHTIG: Pushe deine Aenderungen in denselben bestehenden Branch `feature/{ticket.id}`. Erstelle KEINEN neuen Branch. Der Branch existiert bereits auf dem Remote.

"""
        if pipeline_status == "failed":
            retry_context += "- **Pipeline fehlgeschlagen** – Bitte stelle sicher, dass alle Tests und Typechecks bestehen.\n"
        if review_notes:
            retry_context += f"- **Review-Feedback:** {review_notes}\n"
        if mr_url:
            retry_context += f"- **MR-Link:** {mr_url}\n"
        conflict_status = analysis.get("conflict_status", "")
        if conflict_status == "conflict_detected":
            retry_context += "- **Merge-Konflikt** – Der Branch hat Konflikte mit dem Ziel-Branch. Loese die Konflikte auf, rebase auf den Ziel-Branch und pushe mit Force-Push.\n"

    repo_summaries = "\n".join(
        f"  • **{r.name}** – {r.description or 'Keine Beschreibung'} (Tags: {', '.join(r.tags)})"
        for r in repos
    )

    return f"""# 🎯 Aufgabe: {ticket.id} – {ticket.title}

## Prioritaet: {ticket.priority} | Typ: {ticket.issue_type} | Komplexitaet: {complexity} (~{estimates}h)

## Primaeres Repository: `{primary}`
{retry_context}
## Beschreibung
{ticket.description}

## Ausgewaehlte Repositories ({len(repos)})
{repo_summaries}

## Bewertung
{reasoning}

## Aufgaben
1. Code-Aenderungen in den oben genannten Repositories vornehmen.
2. Unit-/Integration-Tests ergaenzen.
3. Commit mit aussagekraeftiger Nachricht (Conventional Commits).
4. Branch `feature/{ticket.id}` pushen (Force-Push falls der Branch bereits existiert).
5. Merge-Request erstellen oder aktualisieren (Titel = Ticket-Titel, Beschreibung = Aenderungszusammenfassung).

## Akzeptanzkriterien
- [ ] Ticket-Anforderung vollstaendig umgesetzt.
- [ ] Tests decken die Aenderung ab.
- [ ] Saubere Commits in allen betroffenen Repos.
- [ ] Keine Regressionen.
- [ ] Typecheck und Lint bestehen (falls CI vorhanden).

## Hinweise
- Halte dich an bestehende Coding-Conventions.
- Falls unklar: Architektur und Schnittstellen analysieren.
- Beachte den Tech-Stack.
"""

    instructions_raw = ""
    _agent_id = getattr(ticket, 'agent_id', None) or analysis.get("agent_id")
    if _agent_id:
        agent_instrs = get_agent_assigned_instructions(_agent_id)
        if agent_instrs:
            instructions_raw = "\n\n".join(i["content"] for i in agent_instrs)
    if not instructions_raw:
        instructions_raw = get_enabled_agent_instructions()
    if instructions_raw:
        prompt += f"""

## Agent-Anweisungen
{instructions_raw}
"""

    return prompt


# ── Agent Pod Spawner ────────────────────────────────────────────

def _kubectl(args: str) -> Tuple[int, str, str]:
    try:
        result = subprocess.run(f"kubectl {args}", shell=True, capture_output=True, text=True, timeout=30)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return -100, "", "kubectl nicht gefunden"
    except subprocess.TimeoutExpired:
        return -101, "", "Timeout"


def _ensure_ollama_secret():
    """Erstellt K8s Secret mit Ollama Cloud API Key falls nicht vorhanden."""
    if not OLLAMA_CLOUD_API_KEY:
        return False
    
    rc, _, _ = _kubectl(f"get secret ollama-cloud-api-key -n {AGENT_NAMESPACE} -o name")
    if rc == 0:
        return True
    
    secret_yaml = f"""apiVersion: v1
kind: Secret
metadata:
  name: ollama-cloud-api-key
  namespace: {AGENT_NAMESPACE}
type: Opaque
stringData:
  api-key: "{OLLAMA_CLOUD_API_KEY}"
"""
    secret_path = Path("/tmp/ollama-cloud-secret.yaml")
    secret_path.write_text(secret_yaml, encoding="utf-8")
    rc, _, err = _kubectl(f"apply -f {secret_path}")
    if rc != 0:
        raise RuntimeError(f"Ollama Cloud Secret konnte nicht erstellt werden: {err}")
    log.ok("Ollama Cloud Secret erstellt")
    return True


def _sanitize_yaml_value(val: str) -> str:
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', str(val))


def spawn_agent_pod(ticket: Ticket, selected: List[RepoConfig], assignment_md: str, analysis: Dict):
    pod_name = f"agent-worker-{ticket.id.lower()}"
    repos_json = json.dumps({r.name: {"url": r.url, "branch": r.branch} for r in selected}, indent=2, ensure_ascii=False)
    escaped_assignment = assignment_md.replace("\\", "\\\\").replace('"', '\\"')
    GIT_USER = _sanitize_yaml_value(get_setting("git_user") or os.getenv("GIT_USER", "gitlab-ci-token"))
    GITLAB_HOST_SAFE = _sanitize_yaml_value(get_setting("git_host") or os.getenv("GITLAB_HOST", "gitlab.example.com"))
    GITLAB_TOKEN_SAFE = _sanitize_yaml_value(get_setting("git_token") or GITLAB_TOKEN)

    log.step("Agent-Pod erzeugen")

    rc, out, err = _kubectl(f"get namespace {AGENT_NAMESPACE} -o name")
    if rc != 0:
        log.info(f"Namespace {AGENT_NAMESPACE} existiert nicht → erstelle...")
        rc2, _, err2 = _kubectl(f"create namespace {AGENT_NAMESPACE}")
        if rc2 != 0:
            raise RuntimeError(f"Namespace {AGENT_NAMESPACE} konnte nicht erstellt werden: {err2}")

    # Ollama Secret sicherstellen
    has_ollama_secret = _ensure_ollama_secret()

    # ConfigMap: repos
    log.sub("Erstelle ConfigMap: repos")
    repos_cm_yaml = f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: {pod_name}-repos
  namespace: {AGENT_NAMESPACE}
  labels:
    ticket-id: "{ticket.id}"
data:
  repos.json: |
{chr(10).join('    ' + line for line in repos_json.splitlines())}
"""
    repos_path = Path("/tmp/agent-cm-repos.yaml")
    repos_cm_yaml = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', repos_cm_yaml)
    repos_path.write_text(repos_cm_yaml, encoding="utf-8")
    rc, out, err = _kubectl(f"apply -f {repos_path}")
    if rc != 0:
        raise RuntimeError(f"ConfigMap repos konnte nicht angelegt werden: {err}")
    log.ok("ConfigMap repos angelegt")

    # ConfigMap: assignment
    log.sub("Erstelle ConfigMap: assignment")
    assignment_cm_yaml = f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: {pod_name}-assignment
  namespace: {AGENT_NAMESPACE}
  labels:
    ticket-id: "{ticket.id}"
data:
  task.md: |
{chr(10).join('    ' + line for line in assignment_md.splitlines())}
"""
    assignment_path = Path("/tmp/agent-cm-assignment.yaml")
    assignment_cm_yaml = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', assignment_cm_yaml)
    assignment_path.write_text(assignment_cm_yaml, encoding="utf-8")
    rc, out, err = _kubectl(f"apply -f {assignment_path}")
    if rc != 0:
        raise RuntimeError(f"ConfigMap assignment konnte nicht angelegt werden: {err}")
    log.ok("ConfigMap assignment angelegt")

    # ConfigMap: opencode.json
    log.sub("Erstelle ConfigMap: opencode")

    agent_id = ticket.agent_id if hasattr(ticket, 'agent_id') and ticket.agent_id else None
    if agent_id:
        agent_mcps = get_agent_mcp_servers(agent_id)
        mcp_servers = agent_mcps if agent_mcps else get_enabled_mcp_servers()
    else:
        mcp_servers = get_enabled_mcp_servers()
    mcp_entries = {}
    for srv in mcp_servers:
        cmd = srv.get("command", "").split()
        entry = {"type": srv.get("server_type", "local"), "command": cmd, "enabled": True}
        args_raw = srv.get("args", "[]")
        if isinstance(args_raw, str):
            try:
                args_list = json.loads(args_raw)
                if args_list:
                    entry["args"] = args_list
            except (json.JSONDecodeError, TypeError):
                pass
        env_raw = srv.get("env", "{}")
        if isinstance(env_raw, str):
            try:
                env_dict = json.loads(env_raw)
                if env_dict:
                    entry["environment"] = env_dict
            except (json.JSONDecodeError, TypeError):
                pass
        mcp_entries[srv["name"]] = entry

    plugin_names = get_enabled_plugin_names()
    plugin_json = json.dumps(plugin_names)

    mcp_servers_json = json.dumps(mcp_entries) if mcp_entries else "{}"

    opencode_config = {
        "$schema": "https://opencode.ai/config.json",
        "model": f"ollama/{OPENCODE_MODEL}",
        "small_model": f"ollama/{OPENCODE_MODEL}",
        "autoupdate": False,
        "share": "disabled",
        "plugin": plugin_names,
        "provider": {
            "ollama": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Ollama",
                "options": {
                    "baseURL": OLLAMA_BASE_URL
                },
                "models": {
                    OPENCODE_MODEL: {
                        "name": OPENCODE_MODEL,
                        "options": {
                            "num_ctx": 32768
                        }
                    }
                }
            }
        },
        "mcp": mcp_entries if mcp_entries else {}
    }
    opencode_json_str = json.dumps(opencode_config, indent=2, ensure_ascii=False)
    opencode_indented = chr(10).join("    " + line for line in opencode_json_str.splitlines())

    opencode_cm_yaml = f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: {pod_name}-opencode
  namespace: {AGENT_NAMESPACE}
  labels:
    ticket-id: "{ticket.id}"
data:
  opencode.json: |
{opencode_indented}
"""
    opencode_path = Path("/tmp/agent-cm-opencode.yaml")
    opencode_cm_yaml = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', opencode_cm_yaml)
    opencode_path.write_text(opencode_cm_yaml, encoding="utf-8")
    rc, out, err = _kubectl(f"apply -f {opencode_path}")
    if rc != 0:
        raise RuntimeError(f"ConfigMap opencode konnte nicht angelegt werden: {err}")
    log.ok("ConfigMap opencode angelegt")

    # ConfigMap: memory blocks (agent-specific, DB-persisted)
    memory_md = ""
    if agent_id:
        try:
            memory_md = get_agent_memory_as_markdown(agent_id, "")
        except Exception:
            memory_md = ""
    if not memory_md:
        memory_md = """---
label: persona
description: Agent-Identitaet und Verhalten
limit: 5000
read_only: false
---
Du bist ein autonomer Software-Entwickler. Arbeite sorgfaeltig und methodisch.

---
label: human
description: Praeferenzen des Operators
limit: 5000
read_only: false
---
Bevorzuge deutsche UI-Sprache. Verwende Conventional Commits. Tests sind Pflicht.

---
label: project
description: Projekt-Konventionen und Architektur
limit: 5000
read_only: false
---
Tech-Stack: Vue 3 + TypeScript Frontend, Go Backend.
Tests: pnpm test && vue-tsc --noEmit (Frontend), go test ./... (Backend).
"""

    memory_cm_yaml = f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: {pod_name}-memory
  namespace: {AGENT_NAMESPACE}
  labels:
    ticket-id: "{ticket.id}"
data:
  memory.md: |
{chr(10).join('    ' + line for line in memory_md.splitlines())}
"""
    memory_path = Path("/tmp/agent-cm-memory.yaml")
    memory_cm_yaml = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', memory_cm_yaml)
    memory_path.write_text(memory_cm_yaml, encoding="utf-8")
    rc, out, err = _kubectl(f"apply -f {memory_path}")
    if rc != 0:
        raise RuntimeError(f"ConfigMap memory konnte nicht angelegt werden: {err}")
    log.ok("ConfigMap memory angelegt")

    complexity = analysis.get("complexity", "Medium")
    primary = analysis.get("primary_repo", selected[0].name)

    log.sub("Erstelle Agent-Pod")

    # Env-Var fuer Ollama Secret
    ollama_env = ""
    if has_ollama_secret:
        ollama_env = """        - name: OLLAMA_CLOUD_API_KEY
          valueFrom:
            secretKeyRef:
              name: ollama-cloud-api-key
              key: api-key"""

    pod_yaml = f"""apiVersion: v1
kind: Pod
metadata:
  name: {pod_name}
  namespace: {AGENT_NAMESPACE}
  labels:
    app.kubernetes.io/name: hivemind
    app.kubernetes.io/component: agent
    ticket-id: "{ticket.id}"
spec:
  restartPolicy: Never
  volumes:
    - name: workspace
      emptyDir: {{}}
    - name: repos-config
      configMap:
        name: {pod_name}-repos
    - name: task-prompt
      configMap:
        name: {pod_name}-assignment
    - name: opencode-config
      configMap:
        name: {pod_name}-opencode
    - name: memory-blocks
      configMap:
        name: {pod_name}-memory

  initContainers:
    - name: clone-repos
      image: {AGENT_IMAGE}
      imagePullPolicy: IfNotPresent
      volumeMounts:
        - name: workspace
          mountPath: /workspace
        - name: repos-config
          mountPath: /config
      env:
        - name: GITLAB_HOST
          value: "{GITLAB_HOST_SAFE}"
        - name: GITLAB_TOKEN
          value: "{GITLAB_TOKEN_SAFE}"
        - name: GIT_USER
          value: "{GIT_USER}"
      command: ["/bin/bash", "-c"]
      args:
        - |
          set -euo pipefail
          for repo in $(jq -r 'keys[]' /config/repos.json); do
            url=$(jq -r --arg r "$repo" '.[$r].url' /config/repos.json)
            branch=$(jq -r --arg r "$repo" '.[$r].branch' /config/repos.json)
            if echo "$url" | grep -qE "^https?://"; then
              url=$(echo "$url" | sed -E "s|^(https?://)|\\1${{GIT_USER}}:${{GITLAB_TOKEN}}@|")
            fi
            echo "Cloning $repo (Branch: $branch) ..."
            git clone -b "$branch" --single-branch "$url" "/workspace/$repo"
            echo "Init leankg $repo ..."
            cd "/workspace/$repo"
            leankg init
            echo "Index leankg $repo ..."
            leankg index .
          done
          echo "All repos processed"

  containers:
    - name: opencode-agent
      image: {AGENT_IMAGE}
      imagePullPolicy: IfNotPresent
      volumeMounts:
        - name: workspace
          mountPath: /workspace
        - name: task-prompt
          mountPath: /etc/task
        - name: opencode-config
          mountPath: /mnt/opencode-config
        - name: memory-blocks
          mountPath: /mnt/memory-blocks
      env:
        - name: GITLAB_TOKEN
          value: "{GITLAB_TOKEN_SAFE}"
        - name: GITLAB_HOST
          value: "{GITLAB_HOST_SAFE}"
        - name: GIT_USER
          value: "{GIT_USER}"
        - name: GITLAB_USER
          value: "{GIT_USER}"
        - name: OLLAMA_BASE_URL
          value: "{OLLAMA_BASE_URL}"
        - name: OPENCODE_MODEL
          value: "{OPENCODE_MODEL}"
        - name: OPENCODE_PLUGINS
          value: '{plugin_json}'
{ollama_env}
        - name: DRY_RUN
          value: "false"
        - name: OPENCODE_PERMISSION_WRITE
          value: "allow"
        - name: OPENCODE_PERMISSION_BASH
          value: "allow"
        - name: OPENCODE_PERMISSION_EXTERNAL_DIRECTORY
          value: "allow"
        - name: OPENCODE_PERMISSION_DOOM_LOOP
          value: "allow"
        - name: ORCHESTRATOR_URL
          value: "http://orchestrator.{AGENT_NAMESPACE}.svc.cluster.local:8080"
        - name: TICKET_ID
          value: "{ticket.id}"
        - name: AGENT_ID
          value: "{agent_id or ''}"
        - name: OPENCODE_SERVER_PASSWORD
          value: "{os.getenv('OPENCODE_SERVER_PASSWORD', '')}"
        - name: COMMENT_POLL_INTERVAL
          value: "{os.getenv('COMMENT_POLL_INTERVAL', '30')}"
      ports:
        - name: opencode-web
          containerPort: 4096
      resources:
        requests:
          cpu: "500m"
          memory: "1Gi"
        limits:
          cpu: "4"
          memory: "8Gi"
"""

    pod_path = Path("/tmp/agent-pod.yaml")
    pod_yaml_clean = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', pod_yaml)
    pod_path.write_text(pod_yaml_clean, encoding="utf-8")

    # Delete existing pod if it exists (spec changes are not allowed)
    rc_del, _, _ = _kubectl(f"delete pod {pod_name} -n {AGENT_NAMESPACE} --force --grace-period=0 2>/dev/null")
    if rc_del == 0:
        log.info(f"Bestehenden Pod {pod_name} gelöscht, erstelle neuen...")
        import time
        time.sleep(2)

    rc, out, err = _kubectl(f"apply -f {pod_path}")
    if rc != 0:
        raise RuntimeError(f"Agent-Pod konnte nicht gestartet werden: {err}")
    log.ok(f"Agent-Pod {pod_name} gestartet")

    log.info(f"Ticket: {ticket.id} – {ticket.title}")
    log.info(f"Komplexitaet: {complexity} | Primaer: {primary}")
    log.info(f"Repos ({len(selected)}): {', '.join(r.name for r in selected)}")
    log.info(f"Pod-Status: kubectl -n {AGENT_NAMESPACE} get pod {pod_name} -w")
    return True


# ── Orchestrator ───────────────────────────────────────────────────

class Orchestrator:
    def __init__(self, config_path: str):
        self.config = OrchestratorConfig.from_file(config_path)
        Path(self.config.pvc_mount_path).mkdir(parents=True, exist_ok=True)
        self.git = RepoManager(self.config.pvc_mount_path, self.config.track_branch)
        self.leankg = LeanKGManager(self.config)
        self.llm = OllamaClient(self.config.ollama_host, self.config.ollama_model)
        self._statuses: List[RepoStatus] = []

    def init(self):
        log.step("=== Orchestrator Initialisierung ===")
        log.info(f"Work-Dir:  {self.config.work_dir}")
        log.info(f"PVC-Pfad:  {self.config.pvc_mount_path}")
        log.info(f"Branch:    {self.config.track_branch}")
        log.info(f"Repos:     {len(self.config.repositories)}")

        configure_git_credentials()

        self._statuses = self.git.init_all(self.config.repositories)

        if self.config.leankg_enabled:
            self.leankg.index_all(self._statuses)

        log.step("Initialisierung abgeschlossen")

    def update(self):
        log.step("=== Repository Update ===")
        self._statuses = self.git.update_all(self.config.repositories)

        changed = [s for s in self._statuses if s.changes_detected]
        if changed:
            log.info(f"{len(changed)} Repos hatten Aenderungen → Re-Index via LeanKG")
            if self.config.leankg_enabled:
                for s in changed:
                    if not s.error and self.leankg.index_repo(s.local_path):
                        s.leankg_ready = True
                        log.ok(f"{s.config.name}: re-indiziert")
        else:
            log.info("Keine Aenderungen erkannt.")
        return self._statuses

    def process(self, ticket: Ticket, use_llm: bool, skip_clone: bool) -> Path:
        self.update()

        log.step("Auswahl der benoetigten Repositories")
        analysis = None

        if use_llm and self.llm.is_available():
            log.info("Verwende Ollama...")
            try:
                analysis = self.llm.analyze_repos_for_ticket(ticket, self._statuses, self.leankg)
                log.info(f"LLM-Auswahl:    {analysis.get('selected_repos', [])}")
                log.info(f"Primaer:          {analysis.get('primary_repo')}")
                log.info(f"Komplexitaet:     {analysis.get('complexity')}")
            except RuntimeError as e:
                log.error(f"LLM-Fehler: {e}")
                analysis = None
        elif use_llm:
            log.error(f"Ollama nicht erreichbar ({self.llm.host})")

        if not analysis:
            log.error("Keine KI-Analyse verfuegbar – Abbruch")
            return None

        selected_names = set(analysis.get("selected_repos", []))
        selected_configs = [r for r in self.config.repositories if r.name in selected_names]

        log.step("Prompt generieren")
        prompt = generate_assignment_prompt(ticket, analysis, selected_configs)

        set_ticket_ai_planning(ticket.id, analysis)

        log.step("Workspace bauen")
        workspace_dir = Path(self.config.work_dir) / f"workspace_{ticket.id}"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        create_opencode_config(workspace_dir / ".opencode", ticket, selected_configs, analysis, prompt)
        create_launch_scripts(workspace_dir)

        log.step("Agent-Pod starten")
        spawn_agent_pod(ticket, selected_configs, prompt, analysis)

        return workspace_dir


# ── CLI ───────────────────────────────────────────────────────────

def print_usage():
    print("""Usage: python main.py <command> [...]

Commands:
  init                         Alle Repos pullen + LeanKG indexieren
  init-repos                   GitLab-Projekte importieren + KI Tags/Beschreibung
  update                       Alle Repos updaten + Delta + Re-Index
  process <ticket.json> [...]  Ticket analysieren + Agent-Pod im Cluster starten
  serve                        HTTP-Server fuer Ticket-API starten

(process Flags: --llm, --llm-only, --no-clone, --leankg-only)

Environment (aus .env oder direkt):
  ORCHESTRATOR_CONFIG=/app/config/orchestrator_config.json
  AGENT_NAMESPACE=hivemind
  AGENT_IMAGE=hivemind-opencode:v2
  GITLAB_TOKEN=glxxxxxxxxxxxxxxxxxxxxxx
  OPENCODE_MODEL=opencode-go/deepseek-v4-pro
""")
    sys.exit(1)


def main():
    args = sys.argv[1:]
    if not args:
        print_usage()

    cmd = args[0]
    remaining = args[1:]

    if cmd == "init":
        orch = Orchestrator(ORCHESTRATOR_CONFIG)
        orch.init()
        return

    if cmd == "init-repos":
        import_repos_from_config(ORCHESTRATOR_CONFIG)
        gitlab_host = os.getenv("GITLAB_HOST", os.getenv("GIT_HOST", ""))
        gitlab_token = os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))
        if not gitlab_host or not gitlab_token:
            print("❌ GITLAB_HOST und GITLAB_TOKEN muessen gesetzt sein")
            sys.exit(1)

        import urllib.parse as _urlparse
        import urllib.request as _urlreq
        import urllib.error as _urlerr

        base = f"https://{gitlab_host}/api/v4/projects?membership=true&min_access_level=20&per_page=100&order_by=name"
        req = _urlreq.Request(base, headers={"PRIVATE-TOKEN": gitlab_token})
        try:
            with _urlreq.urlopen(req, timeout=30) as resp:
                projects = json.loads(resp.read())
        except Exception as e:
            print(f"❌ GitLab API Fehler: {e}")
            sys.exit(1)

        from database import get_all_repos as _get_all_repos, add_repo as _add_repo, get_repo as _get_repo
        existing = {r["name"] for r in _get_all_repos()}
        added = 0
        for p in projects:
            name = p.get("name", "")
            if not name or name in existing:
                continue
            url = p.get("http_url_to_repo", "")
            branch = p.get("default_branch", "main")
            description = p.get("description", "")
            topics = p.get("topics", [])
            _add_repo(name=name, url=url, branch=branch, description=description, tags=topics)
            existing.add(name)
            added += 1
            print(f"  + {name}: {description[:60]}")

        print(f"\n✅ {added} Repos importiert, {len(projects) - added} bereits vorhanden")
        return

    if cmd == "update":
        orch = Orchestrator(ORCHESTRATOR_CONFIG)
        orch.init()
        return

    if cmd == "serve":
        import uvicorn
        raw_port = os.getenv("ORCHESTRATOR_PORT", "8080")
        if raw_port.startswith("tcp://"):
            raw_port = raw_port.split(":")[-1]
        PORT = int(raw_port)
        print(f"🌐 Orchestrator FastAPI-Server auf Port {PORT}")
        uvicorn.run("server:app", host="0.0.0.0", port=PORT, reload=False)
        return

    if cmd == "process":
        if not remaining:
            print("❌ Fehlendes Ticket-Argument")
            print_usage()

        ticket_path = remaining[0]
        flags = set(remaining[1:])
        use_llm = "--llm" in flags or "--llm-only" in flags
        skip_clone = "--no-clone" in flags

        if not Path(ticket_path).is_file():
            print(f"❌ Datei nicht gefunden: {ticket_path}")
            sys.exit(1)

        ticket = Ticket.from_json(ticket_path)
        print(f"📋 Ticket geladen: {ticket.id} – {ticket.title}")

        orch = Orchestrator(ORCHESTRATOR_CONFIG)
        orch.init()
        orch.process(ticket, use_llm=use_llm, skip_clone=skip_clone)
        return

    # Legacy: Ticket-Datei direkt als erstes Argument
    if Path(cmd).is_file():
        flags = set(remaining)
        ticket = Ticket.from_json(cmd)
        print(f"📋 Ticket geladen: {ticket.id} – {ticket.title}")
        orch = Orchestrator(ORCHESTRATOR_CONFIG)
        orch.init()
        orch.process(ticket, use_llm="--llm" in flags, skip_clone="--no-clone" in flags)
        return

    print_usage()


if __name__ == "__main__":
    main()
