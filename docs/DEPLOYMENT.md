# HiveMind Dokumentation

Automatisierte AI-Coding-Agenten für Kubernetes. Ticket rein → Agent-Pod startet → Code-Änderungen + MR raus.

---

## Architektur-Übersicht

```
┌──────────────────────────────────────────────┐
│  Jira / GitLab Issues / HTTP API             │
│  (Ticket mit Titel, Beschreibung, Labels)   │
└──────────────┬─────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│  Orchestrator (K8s Deployment)                 │
│                                              │
│  1. Ticket laden & analysieren               │
│  2. Relevante Repos via LLM/Heuristik finden│
│  3. Assignment-Prompt generieren             │
│  4. kubectl apply → ConfigMaps + Agent-Pod  │
└──────────────┬─────────────────────────────────┘
               │
               ▼ kubectl apply (RKE2 Cluster)
┌──────────────────────────────────────────────┐
│  Agent-Pod: agent-worker-<TICKET-ID>          │
│  (restartPolicy: Never → terminiert nach Task) │
│                                              │
│  ┌─ Init Container ─┐                        │
│  │ git clone (nur  │                        │
│  │  relevante Repo) │                        │
│  └──────┬────────────┘                        │
│         │ Shared Volume /workspace             │
│  ┌──────▼──────────────────────────────┐      │
│  │ Main Container: hivemind-opencode │      │
│  │                                    │      │
│  │ 1. opencode run "$(task.md)"       │      │
│  │ 2. git checkout -b feature/<ID>     │      │
│  │ 3. git commit + push                │      │
│  │ 4. @gitbeaker/rest → MR erstellen  │      │
│  │ 5. Pod terminiert                  │      │
│  └────────────────────────────────────┘      │
└──────────────────────────────────────────────┘
```

---

## Komponenten

### 1. Orchestrator (`Orchestrator/`)

Python-3-Skript im K8s-Cluster. Analysiert Tickets, erzeugt Agent-Pods.

| Befehl | Zweck |
|--------|-------|
| `init` | Alle Repos pullen + LeanKG indexieren |
| `update` | Repos aktualisieren + Delta + Re-Index |
| `process ticket.json` | Ticket analysieren → ConfigMaps + Agent-Pod erzeugen |
| `serve` | HTTP-Server auf Port 8080 |

**Konfiguration:**
```bash
ORCHESTRATOR_CONFIG=/app/config/orchestrator_config.json
AGENT_NAMESPACE=hivemind
AGENT_IMAGE=hivemind-opencode:latest
GITLAB_TOKEN=glpat-xxxxxxxx
OPENCODE_MODEL=opencode-go/deepseek-v4-pro
```

### 2. Agent (`Agent/`)

Node.js-Container mit opencode + git + @gitbeaker/rest.

**Dockerfile:**
- `node:22-slim` Basis
- `git`, `jq`, `ca-certificates`, `curl` installiert
- `@gitbeaker/rest` für GitLab API (MR erstellen)
- `opencode-ai` global installiert
- `kubectl` für RKE2-Kompatibilität

**Entrypoint (`entrypoint.sh`):**
1. Assignment-Prompt aus ConfigMap lesen
2. Ticket-ID + Titel extrahieren
3. `opencode run "$(cat task.md)" --dangerously-skip-permissions`
4. Pro geändertem Repo: Branch, Commit, Push, MR
5. Target-Branch: `development`

### 3. RBAC (`Orchestrator/helm/orchestrator/templates/rbac.yaml`)

Der Orchestrator braucht im K8s-Cluster Rechte:
- ConfigMaps erstellen/löschen
- Pods erstellen/deleten/watch

---

## Deployment im RKE2 Cluster

### Vorbereitung

**1. GitLab Secret anlegen:**
```bash
kubectl create namespace hivemind 2>/dev/null || true

# SSH-Key des Agent-Accounts + Personal Access Token
kubectl create secret generic gitlab-agent-credentials \
  --from-file=ssh-privatekey=/path/to/agent_id_rsa \
  --from-literal=GITLAB_TOKEN=glpat-xxxxxxxx \
  -n hivemind
```

**2. Orchestrator-ConfigMap anlegen:**
```bash
kubectl create configmap orchestrator-config \
  --from-file=orchestrator_config.json=Orchestrator/orchestrator_config.json \
  -n hivemind
```

### Option A: Helm Chart

```bash
helm upgrade --install orchestrator ./Orchestrator/helm/orchestrator \
  --namespace hivemind \
  --set image.repository=your-registry/orchestrator \
  --set agentImage=your-registry/hivemind-opencode:latest \
  --set rbac.create=true
```

### Option B: Kubectl (kombiniertes Manifest)

```bash
# Vorher Secrets anlegen (siehe oben)
kubectl apply -f Orchestrator/k8s/orchestrator.yaml
```

### Option C: Docker Compose (nur lokal/testen)

```bash
# .env anpassen:
cp .env.example .env
# → GITLAB_TOKEN, SSH_KEY_PATH eintragen
# → DRY_RUN=true für Test-Modus

# Alles in einem Rutsch:
docker compose --profile full up

# Oder schrittweise:
docker compose --profile init up orchestrator-init
docker compose --profile process up orchestrator-process
docker compose --profile run up agent
```

---

## Ticket einreichen → Agent startet automatisch

### Via HTTP (zukünftig)
```bash
curl -X POST http://orchestrator:8080/process \
  -H "Content-Type: application/json" \
  -d @ticket.json
```

### Via kubectl exec (direkt im Cluster)
```bash
kubectl exec -n hivemind deploy/orchestrator -- \
  python3 /app/main.py process /app/tickets/example_ticket.json
```

### Via lokal (wenn kubectl gegen RKE2 konfiguriert)
```bash
python3 Orchestrator/main.py process Orchestrator/example_ticket.json
```

**Status verfolgen:**
```bash
kubectl -n hivemind logs -f agent-worker-proj-123
kubectl -n hivemind get pods -l app.kubernetes.io/component=agent
```

---

## Konfigurationsdateien

### `Orchestrator/orchestrator_config.json`

```json
{
  "work_dir": "/app/workspace",
  "pvc_mount_path": "/app/workspace/repos",
  "track_branch": "main",
  "auto_pull_interval_minutes": 60,
  "leankg_enabled": true,
  "ollama_host": "http://ollama:11434",
  "ollama_model": "gemma4:26b",
  "repositories": [
    {
      "name": "gateway",
      "url": "git@YOUR_GITLAB_HOST:example-org/services/gateway.git",
      "branch": "main",
      "description": "API-Gateway ...",
      "tags": ["backend", "gateway", "api", "routing"]
    }
  ]
}
```

### `Agent/opencode.json`

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "opencode-go/deepseek-v4-pro",
  "permission": { "*": "allow" }
}
```

---

## Wie funktioniert die Repo-Auswahl?

1. **LLM-Analyse** (falls Ollama erreichbar): Das Modell bekommt Ticket + Repo-Beschreibungen und wählt die relevanten repos.
2. **Heuristischer Fallback** (wenn kein Ollama): Schlüsselwort-Matching auf Tags + Beschreibungen.

---

## Sicherheit

| Aspekt | Maßnahme |
|--------|----------|
| Git-Zugriff | SSH-Key per Secret, eigener GitLab-Account |
| API-Token | Personal Access Token per Secret |
| K8s-Rechte | Orchestrator nur via ServiceAccount/Role RBAC |
| opencode | `--dangerously-skip-permissions` (nur im Pod) |
| Network | Pod terminiert nach Abschluss (keine Idle-Ressourcen) |

---

## Troubleshooting

| Problem | Lösung |
|---------|--------|
| Agent-Pod nicht gestartet | `kubectl logs -n hivemind deploy/orchestrator` |
| Git clone fehlgeschlagen | SSH-Key + Secret checken, Host key akzeptieren |
| GitLab API 403 | Token hat keine `api`-Scope-Rechte für MR-Erstellung |
| opencode-Fehler | `kubectl logs -f -n hivemind agent-worker-<ID>` |
| LeanKG fehlt | `leankg` nicht im Orchestrator-Image, Dockerfile checken |
| PVC-Fehler | `storageClassName: local-path` für RKE2 anpassen |

