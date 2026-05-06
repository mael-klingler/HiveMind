# HiveMind

**Submit a ticket, get a merge request. AI coding agents that run themselves on Kubernetes.**

[![Version](https://img.shields.io/badge/version-0.7.0-blue)](.version)
[![CI](https://img.shields.io/badge/CI-GitHub_Actions-2088FF)](.github/workflows/ci.yaml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![K8s](https://img.shields.io/badge/platform-Kubernetes-326ce5)](Orchestrator/k8s/)

> **The problem:** You prompt an AI agent, it writes code, you push a branch, someone reviews the MR and says "fix this" — and the loop breaks. The agent session is gone. Context is lost. Start over.
>
> **HiveMind closes the loop.** Agent picks up your review comments, pushes a fix. Pipeline fails? Agent self-corrects. Merge conflict? Agent rebases. All in isolated K8s pods, in parallel, across multiple repos. Nobody babysits a terminal.

<p align="center">
  <img src="docs/images/dashboard.png" alt="HiveMind Dashboard" width="800">
</p>

---

## 30-Second Demo

```bash
# 1. Deploy (5 minutes)
./setup.sh && ./redeploy.sh

# 2. Submit a ticket
curl -X POST http://localhost:8080/api/tickets \
  -H "Content-Type: application/json" \
  -d '{"id":"PROJ-123","title":"Fix login error on mobile"}'

# 3. Watch the agent work — it clones, analyzes, codes, tests, pushes, opens the MR
# 4. You review the MR — agent automatically addresses your feedback
# 5. You merge — done
```

---

## What It Does — By Example

| You want this | How HiveMind handles it |
|---|---|
| **Bug fix across frontend + backend** | LLM analyzes both repos, picks relevant ones, agent works across all of them in one session |
| **10 backlog items, 1 senior dev** | Submit 10 tickets — N agents work in parallel, each in its own isolated pod |
| **CI pipeline fails on the MR** | Agent detects the failure, re-queues itself with "fix the tests" context, pushes a fix |
| **Reviewer says "rename this variable"** | Review comment triggers a follow-up agent session, pushes the rename |
| **Merge conflict on the branch** | Agent detects the conflict, rebases, resolves, and force-pushes |
| **New team member joins** | Agents carry memory blocks (project conventions, tech stack, preferences) — no hand-holding needed |

---

## What Makes This Different from Copilot/Cursor?

| | ChatGPT / Copilot / Cursor | HiveMind |
|---|---|---|
| Who runs it? | You, one prompt at a time | Orchestrator, in parallel |
| Where does it run? | Your laptop, your working tree | Isolated K8s pod per task |
| How many tasks? | One at a time, one person | N agents, N tickets simultaneously |
| What happens when it fails? | You re-prompt from scratch | Auto-retry with full context (3 retries) |
| What about review feedback? | Copy-paste into a new session | Agent picks up comments automatically |
| Multi-repo? | No — one repo per session | Yes — LLM selects repos, agent works across all |
| Persistent memory? | No | Per-agent memory blocks survive across sessions |
| Monitoring? | You watch a terminal | Web dashboard, live logs, MR tracking, Prometheus |

---

## How It Works — The Full Loop

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │                         YOU SUBMIT A TICKET                          │
  │               (GitLab/GitHub issue / API / Web UI)                   │
  └──────────────────────────┬───────────────────────────────────────────┘
                             │
                             ▼
  ┌──────────────────────────────────────────────────────────────────────┐
  │                        ORCHESTRATOR PICKS IT UP                      │
  │                                                                      │
  │   1. LLM analyzes the ticket + repo context (Ollama)                 │
  │   2. Selects 1-4 relevant repositories                               │
  │   3. Generates a detailed assignment prompt                          │
  │   4. Spawns an ephemeral K8s pod with everything the agent needs     │
  │      ┌─ ConfigMaps: repos, assignment, opencode config, memory       │ 
  │      └─ Init Container: git clone + LeanKG index                     │ 
  └──────────────────────────┬───────────────────────────────────────────┘
                             │
                             ▼
  ┌──────────────────────────────────────────────────────────────────────┐
  │                       AGENT POD DOES THE WORK                        │
  │                                                                      │
  │   Phase 1:   opencode run — analyzes codebase, makes changes         │
  │   Phase 1.5: runs TEST_COMMAND, self-corrects on failure             │
  │   Phase 2:   git commit + push to feature/TICKET-ID branch           │
  │              creates VCS Merge Request (GitLab / GitHub)             │
  │   Phase 3:   polls for your review comments                          │
  │              runs follow-up sessions to address feedback             │
  │                                                                      │
  │   ┌─────────────────────────────┐  ┌────────────────────────────┐    │
  │   │  opencode web (port 4096)   │  │  Interactive session via   │    │
  │   │  live agent dashboard       │  │  Orchestrator proxy        │    │
  │   └─────────────────────────────┘  └────────────────────────────┘    │
  └──────────────────────────┬───────────────────────────────────────────┘
                             │
                             ▼
  ┌──────────────────────────────────────────────────────────────────────┐
  │                      REVIEW LIFECYCLE MONITOR                        │
  │                                                                      │
  │   watches MR state via VCS API (GitLab / GitHub)                     │
  │   ├── pipeline failed?   → re-queue with "fix the pipeline" context  │
  │   ├── merge conflict?    → re-queue with "rebase and resolve"        │
  │   ├── review comments?   → agent runs follow-up, pushes fixes        │
  │   └── MR merged?         → mark completed, delete pod                │
  └──────────────────────────────────────────────────────────────────────┘
```

---

## What's New in v0.7.0

### Security Hardening
- **API Key Authentication** — Optional `X-API-Key` / `Bearer` token auth via `HIVEMIND_API_KEY` env var; if unset, a warning is logged and all routes remain open (backward compat)
- **Rate Limiting** — In-memory 30 req/min on `POST /api/tickets`, configurable via `RATE_LIMIT_PER_MINUTE`
- **Non-Root Containers** — All images run as UID 1000 (`hivemind`), K8s manifests enforce `runAsNonRoot: true`
- **Network Policies** — Default-deny ingress + egress, agent pods allow DNS and VCS egress only
- **ResourceQuota** — Namespace-level CPU/memory/pod limits
- **Trivy Container Scanning** — CI pipeline scans images for CVEs

### Resilience & Operations
- **Graceful Shutdown** — SIGTERM handler drains loops and closes HTTP clients cleanly
- **Orphan Recovery** — On startup, scans for agent pods without active tickets and re-queues them
- **Configurable Stale Timeout** — `AGENT_STALE_TIMEOUT` env var (default 3600s)
- **SQLite DB Persistence** — PVC-backed `/app/data` for durable state across pod restarts
- **PostgreSQL Support** — K8s manifests for `postgres:16-alpine` StatefulSet; `DATABASE_URL=postgresql://...` auto-switches to `database_pg.py`

### Multi-VCS Provider
- **Pluggable VCS abstraction** — `Orchestrator/vcs/` package with `base.py` (abstract interface), `gitlab.py`, `github.py`
- **GitHub webhook** — `POST /webhooks/github` auto-creates tickets from GitHub issues
- **Backward compatible** — `gitlab_client.py` still works; select provider via `VCS_PROVIDER` env var (default: `gitlab`)

### Agent Test Phase
- **Phase 1.5 (Test)** — Agent runs `TEST_COMMAND` env var before committing; self-corrects on failure

### CLI Tool
- **`cli/hivemind`** — Pure-stdlib Python CLI (277 lines): ticket, agent, repo, stream, version commands

### Model Routing
- **Multi-LLM routing** — `MODEL_ROUTING_ENABLED` + `SIMPLE_MODEL` / `COMPLEX_MODEL` env vars; orchestrator picks model by ticket complexity

### Observability & Collaboration
- **Structured Logging** — JSON logs (`timestamp`, `level`, `message`, `logger`) throughout server.py and main.py
- **Expanded Prometheus Metrics** — `hivemind_agents_running`, `hivemind_agents_idle`, `hivemind_agents_total`
- **Dry-Run Preview** — `POST /api/tickets/preview` shows LLM analysis without spawning a pod
- **Collaboration Schema** — `ticket_groups` + `team_channel_messages` tables for multi-agent coordination

### Helm Chart & CI/CD
- **Helm chart maturity** — Full env var coverage in `values.yaml`, network policy template, security context
- **GitHub Actions CI** — `ci.yaml`: test → build → Trivy scan → deploy
- **Docker Compose full-local** — `--profile full-local` runs orchestrator + PostgreSQL on Docker

---

## Key Features

- **Autonomous feedback loops** — review comments, pipeline failures, and merge conflicts auto-re-queue with context
- **Multi-repo tickets** — LLM picks relevant repos, agent works across all of them in one session
- **GitLab + GitHub** — pluggable VCS provider (`VCS_PROVIDER=gitlab|github`)
- **Persistent agent memory** — persona, preferences, and project context survive across sessions
- **Code intelligence** — LeanKG indexes repos before the agent starts
- **K8s-native** — isolated pods, RBAC, network policies, Prometheus metrics, SSE events
- **Interactive sessions** — proxy to each agent's live opencode web UI at `/agent-session/{ticket_id}/`

---

## Key Components

### Orchestrator (FastAPI / Python 3.11)

The control plane. One long-running K8s Deployment that manages everything:

| Component | What it does |
|-----------|-------------|
| **Queue Processor** | Assigns idle agents to queued tickets, smart selection by repo affinity |
| **Workspace Builder** | LLM-powered analysis (Ollama), selects repos, generates prompts, spawns pods |
| **Review Lifecycle Monitor** | Watches MRs — pipeline failures, merge conflicts, review comments — re-queues with context |
| **Agent Pod Monitor** | Tracks pod health, retries failures (3 retries, 120s delay), auto-cleanup |
| **Orphan Recovery** | On startup, re-queues orphaned agent pods that lost their ticket mapping |
| **Agent Session Proxy** | Forwards opencode web UI from agent pods through `/agent-session/{ticket_id}/` |
| **Web UI** | Dashboard, Kanban board, agent/repo management, settings — live SSE updates |
| **REST API** | Full CRUD for tickets, agents, queue, repos, settings, MCP servers, memory, plugins |
| **Auth Middleware** | Optional API key auth (`HIVEMIND_API_KEY`) + in-memory rate limiting |
| **VCS Webhooks** | GitLab and GitHub webhook receivers, auto-create tickets from issues |
| **Prometheus Metrics** | `hivemind_agents_*`, `hivemind_queue_length`, `hivemind_tickets` gauges |

### Agent (opencode-ai / Node.js 22)

Ephemeral K8s pods (`restartPolicy: Never`) that do the actual work:

| Phase | What happens |
|-------|-------------|
| **1 — Work** | Reads assignment, starts opencode web (port 4096), runs `opencode run` with the task |
| **1.5 — Test** | Runs `TEST_COMMAND` env var, self-corrects on failure before committing |
| **2 — Ship** | Commits to `feature/{TICKET_ID}`, pushes, creates VCS MR via GitLab/GitHub API |
| **3 — Listen** | Polls orchestrator for human review comments, runs follow-up sessions on feedback |

### VCS Provider Abstraction

```
Orchestrator/vcs/
├── __init__.py      # Factory: get_vcs_provider() based on VCS_PROVIDER env
├── base.py          # Abstract VCSProvider interface
├── gitlab.py        # GitLabProvider — projects, MRs, webhooks, branches
└── github.py        # GitHubProvider — repos, PRs, webhooks, GitHub Enterprise
```

Set `VCS_PROVIDER=github` to switch. GitLab remains the default with full backward compatibility via `gitlab_client.py`.

### Init Container

Runs **before** the agent starts: clones selected repos and runs `leankg init` + `leankg index` so the agent has full code knowledge from the first prompt.

---

## Kubernetes Architecture

HiveMind runs entirely on Kubernetes. No external dependencies beyond a VCS (GitLab/GitHub) and an LLM endpoint.

### Deployed Resources

```
namespace/hivemind
serviceaccount/orchestrator
clusterrole/orchestrator-cluster-role     # pods, configmaps, services
clusterrolebinding/orchestrator-cluster-role-binding
secret/orchestrator-env                   # API keys, tokens, model config
secret/gitlab-agent-credentials           # SSH key + VCS PAT
configmap/orchestrator-config              # orchestrator_config.json
pvc/orchestrator-db                        # 1Gi SQLite DB persistence
pvc/orchestrator-repos                     # 10Gi repo storage
networkpolicy/default-deny                 # Default deny all ingress/egress
networkpolicy/agent-egress                 # Allow DNS + VCS egress for agents
resourcequota/hivemind-quota               # Namespace CPU/memory/pod limits
deployment/orchestrator                    # 1 replica, securityContext: UID 1000
service/orchestrator                       # ClusterIP :8080
```

### Security Context

All pods run as non-root user `hivemind` (UID/GID 1000):

```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  runAsGroup: 1000
  fsGroup: 1000
```

### Agent Pod Lifecycle

Each ticket spawns an ephemeral pod:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: agent-worker-{ticket-id}
  namespace: hivemind
  labels:
    app.kubernetes.io/component: agent
spec:
  restartPolicy: Never
  securityContext:
    runAsNonRoot: true
    runAsUser: 1000
    runAsGroup: 1000
    fsGroup: 1000
  volumes:
    - workspace                           # emptyDir: shared init ↔ main
    - repos-config                        # ConfigMap: repos.json
    - task-prompt                         # ConfigMap: assignment.md
    - opencode-config                     # ConfigMap: opencode.json
    - memory-blocks                       # ConfigMap: agent memory
  initContainers:
    - clone-repos                         # git clone + leankg index
  containers:
    - opencode-agent                      # entrypoint → test → opencode → push → MR → feedback
      ports: [4096]                       # opencode web (proxied by orchestrator)
      resources: { requests: 500m/1Gi, limits: 4/8Gi }
```

**Cleanup is automatic:**

| Scenario | What happens |
|----------|-------------|
| Pod succeeds | Ticket marked completed, pod deleted |
| Pod crashes | Ticket re-queued (up to 3 retries, 120s delay) |
| Pod goes stale (> `AGENT_STALE_TIMEOUT`) | Auto-completed, pod cleaned up |
| MR merged | Pod deleted, agent slot freed |
| Orphaned pod (no active ticket) | Recovered on orchestrator restart |

### RBAC (Least Privilege)

- **Orchestrator** can manage pods, configmaps, and services in `hivemind` only
- **Agent pods** have zero K8s RBAC — they cannot access the API server, list pods, or read secrets

---

## Getting Started

### Prerequisites

- Kubernetes cluster (RKE2, k3s, Docker Desktop, minikube, etc.)
- `kubectl` configured and pointing at your cluster
- Docker (for building images)
- GitLab or GitHub instance + Personal Access Token
- Ollama endpoint (or any OpenAI-compatible LLM API)

### Quickstart (5 commands)

```bash
# 1. Configure — interactive, reads from .env, creates K8s secrets
./setup.sh

# 2. Build & deploy — semver bump, Docker build, manifest apply, rollout wait
./redeploy.sh

# 3. Open the dashboard
kubectl port-forward -n hivemind deployment/orchestrator 8080:8080

# 4. Verify
curl http://localhost:8080/healthz   # {"status":"ok"}

# 5. Submit your first ticket
curl -X POST http://localhost:8080/api/tickets \
  -H "Content-Type: application/json" \
  -d '{"id":"PROJ-123","title":"Fix login error on mobile","description":"Login button does not show validation errors on mobile viewport..."}'
```

Open [http://localhost:8080](http://localhost:8080) — you'll see the dashboard with idle agent slots ready. The Kanban board at `/tickets` shows live status.

### More ways to submit tickets

**With API key (if `HIVEMIND_API_KEY` is set):**

```bash
curl -X POST http://localhost:8080/api/tickets \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"id":"PROJ-456","title":"Add dark mode toggle"}'
```

**Via Webhook** — configure your VCS project to send Issue events:
- GitLab: `http://<orchestrator>/webhooks/gitlab`
- GitHub: `http://<orchestrator>/webhooks/github`

**Via CLI:**

```bash
./cli/hivemind ticket create PROJ-123 "Fix login error"
./cli/hivemind stream                    # Live SSE event stream
```

### Watch & Interact

```bash
# Agent pod logs
kubectl -n hivemind logs -f agent-worker-proj-123

# Or access the agent's live opencode session
# Open: http://localhost:8080/agent-session/PROJ-123/
```

The agent will: analyze the codebase → make changes → run tests → commit & push → create MR → wait for your review. When you comment on the MR, the agent picks it up automatically.

### Deploy flags

`./redeploy.sh` handles the full pipeline. Optional flags:

| Flag | What it does |
|------|-------------|
| `--minor` | Bump minor version (0.x.0) |
| `--major` | Bump major version (x.0.0) |
| `--no-cache` | Clean build |
| `--skip-build` | Re-deploy current version without rebuild |
| `--docker` | Build via docker-compose (local testing) |

---

## Configuration

All config flows through `.env` (single source of truth):

| Variable | Description | Default |
|----------|-------------|---------|
| `GITLAB_HOST` | VCS hostname (GitLab or GitHub Enterprise) | `gitlab.com` |
| `GIT_USER` | Git username for HTTPS clone | `gitlab-ci-token` |
| `GITLAB_TOKEN` | VCS Personal Access Token | required |
| `VCS_PROVIDER` | `gitlab` or `github` | `gitlab` |
| `HIVEMIND_API_KEY` | API authentication key (unset = open) | _(unset)_ |
| `RATE_LIMIT_PER_MINUTE` | Rate limit for `POST /api/tickets` | `30` |
| `OLLAMA_HOST` | Ollama endpoint (no `/v1`) | required |
| `OLLAMA_BASE_URL` | Ollama endpoint (with `/v1`) | required |
| `OLLAMA_MODEL` | LLM for orchestrator analysis | required |
| `OPENCODE_MODEL` | LLM for the coding agent | required |
| `OLLAMA_CLOUD_API_KEY` | Cloud LLM key (optional) | _(unset)_ |
| `MODEL_ROUTING_ENABLED` | Enable complexity-based model routing | `false` |
| `SIMPLE_MODEL` | Model for simple tasks | _(unset)_ |
| `COMPLEX_MODEL` | Model for complex tasks | _(unset)_ |
| `AGENT_NAMESPACE` | K8s namespace | `hivemind` |
| `AGENT_IMAGE` | Docker image for agent pods | `hivemind-opencode:latest` |
| `AGENT_MAX_RETRIES` | Max retry attempts | `3` |
| `AGENT_RETRY_DELAY` | Seconds between retries | `120` |
| `AGENT_STALE_TIMEOUT` | Seconds before pod considered stale | `3600` |
| `TEST_COMMAND` | Test command for agent Phase 1.5 | _(unset)_ |
| `DATABASE_URL` | `postgresql://...` for Postgres, else SQLite | _(unset)_ |
| `DRY_RUN` | Preview mode (no pod spawn) | `false` |

---

## REST API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/healthz` | GET | Liveness probe |
| `/readyz` | GET | Readiness probe |
| `/metrics` | GET | Prometheus metrics |
| `/api/tickets` | GET/POST | List or create tickets |
| `/api/tickets/preview` | POST | Dry-run: LLM analysis without spawning pod |
| `/api/tickets/{id}` | GET/PATCH | Get or update a ticket |
| `/api/tickets/{id}/reopen` | POST | Reopen a closed ticket |
| `/api/tickets/{id}/stop` | POST | Stop a running ticket |
| `/api/tickets/{id}/review` | POST | Submit review (approve / changes requested) |
| `/api/tickets/{id}/mr` | POST | Set MR URL |
| `/api/tickets/{id}/logs` | GET | Live agent pod logs |
| `/api/tickets/{id}/comments` | GET/POST | Ticket comments |
| `/api/agents` | GET | List all agents |
| `/api/agents/{id}/progress` | POST | Update agent progress |
| `/api/agents/{id}/complete` | POST | Mark agent task complete |
| `/api/agent-sessions` | GET | List active agent sessions |
| `/api/agent-profiles` | GET/POST | Agent profiles with skills & memory |
| `/api/agent-memory/{id}` | GET/POST/DELETE | Agent memory blocks |
| `/api/repos` | GET/POST | List or add repositories |
| `/api/repos` | PATCH | Bulk update all repos (e.g. switch branch) |
| `/api/repos/{name}` | GET/PUT/PATCH | Get or update a repository |
| `/api/repos/{name}` | DELETE | Remove a repository |
| `/api/repos/{name}/branches` | GET | List repo branches from VCS |
| `/api/config` | GET/POST | Max agent slots, version |
| `/api/settings` | GET/POST | Runtime settings |
| `/api/mcp-servers` | GET/POST/PATCH/DELETE | MCP server config |
| `/api/plugins` | GET/POST/PATCH/DELETE | opencode plugin config |
| `/api/stream` | GET | SSE live event stream |
| `/agent-session/{ticket_id}/` | ANY | Proxy to agent's opencode web UI |
| `/webhooks/gitlab` | POST | GitLab webhook receiver |
| `/webhooks/github` | POST | GitHub webhook receiver |

---

## Project Structure

```
.
├── Agent/                         # The autonomous coding agent
│   ├── Dockerfile                 # Multi-stage: LeanKG + Node.js 22 + opencode (non-root)
│   ├── entrypoint.sh             # Agent lifecycle: setup → test → work → ship → listen
│   ├── opencode.json.template     # opencode config template (envsubst)
│   └── k8s/                       # Manifest templates for manual pod creation
│
├── Orchestrator/                  # The control plane
│   ├── main.py                    # CLI + core: repo manager, LLM client, pod spawner, model routing
│   ├── server.py                  # FastAPI: web UI, REST API, auth, rate limit, SSE, webhooks
│   ├── database.py                # DB router: delegates to SQLite or PostgreSQL
│   ├── database_pg.py             # PostgreSQL backend (ticket_groups, team_channel_messages)
│   ├── k8s_client.py              # Kubernetes API client for pod lifecycle
│   ├── gitlab_client.py            # Backward-compat wrapper → vcs.gitlab.GitLabProvider
│   ├── pod_builder.py             # Pod spec builder (security context, TEST_COMMAND, model routing)
│   ├── vcs/                       # Multi-VCS provider abstraction
│   │   ├── __init__.py            # Factory: get_vcs_provider()
│   │   ├── base.py                # Abstract VCSProvider interface
│   │   ├── gitlab.py              # GitLabProvider implementation
│   │   └── github.py              # GitHubProvider (incl. GH Enterprise)
│   ├── Dockerfile                 # Multi-stage: LeanKG + Python 3.11 (non-root)
│   ├── requirements.txt           # fastapi + uvicorn + httpx + kubernetes + psycopg2-binary
│   ├── static/                    # Dark-themed web UI
│   │   ├── index.html             # Dashboard
│   │   ├── tickets.html           # Kanban board with live logs + agent session links
│   │   ├── agent.html             # Agent profiles, skills, memory, MCP, instructions
│   │   ├── repos.html              # Repo management with branch dropdown from VCS
│   │   └── settings.html           # Settings, pod status, orchestrator control
│   ├── k8s/                       # K8s manifests
│   │   ├── orchestrator.yaml      # Deployment with securityContext, DB persistence
│   │   ├── postgres.yaml           # PostgreSQL StatefulSet for production DB
│   │   ├── db-pvc.yaml             # PVC for SQLite DB persistence
│   │   ├── networkpolicy.yaml      # Default-deny + agent egress
│   │   └── resourcequota.yaml      # Namespace resource limits
│   ├── kustomize/                 # Kustomize base (deployment, RBAC, PVCs, config)
│   ├── helm/orchestrator/         # Helm chart (security context, network policy, env vars)
│   └── tests/                    # Test suite
│       ├── test_hivemind.py        # 438-line comprehensive test suite
│       └── test_unit.py           # Unit tests
│
├── cli/
│   └── hivemind                    # Pure stdlib Python CLI (277 lines)
│
├── .github/workflows/
│   └── ci.yaml                     # CI: test → build → Trivy scan → deploy
│
├── docs/                          # Architecture, deployment, usage, security docs
├── setup.sh                       # Interactive K8s setup (secrets, .env sync)
├── redeploy.sh                    # Versioned build + deploy (semver, image import, rollout)
├── docker-compose.yaml            # Local Docker Compose (full-local profile with PostgreSQL)
├── .env.example                   # Config template
└── .version                       # Current version
```

---

## Documentation

| Document | Content |
|----------|---------|
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Architecture, components, deployment guide (Helm / Kubectl / Docker Compose) |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Data flows, design decisions, class diagrams |
| [`docs/USAGE.md`](docs/USAGE.md) | Submit ticket → follow agent → review MR → debug |
| [`docs/SECURITY.md`](docs/SECURITY.md) | RBAC, non-root containers, network policies, API auth, threat model |

---

## License

MIT