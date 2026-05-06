# HiveMind

**Autonomous AI coding agents on Kubernetes. Ticket in, merge request out.**

[![Version](https://img.shields.io/badge/version-0.7.0-blue)](.version)
[![CI](https://img.shields.io/badge/CI-GitHub_Actions-2088FF)](.github/workflows/ci.yaml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![K8s](https://img.shields.io/badge/platform-Kubernetes-326ce5)](Orchestrator/k8s/)

---

## Why HiveMind?

Your team uses AI coding agents. A developer opens Copilot, writes a prompt, waits, reviews the output, pushes a branch, creates a merge request. Then someone reviews the MR, finds issues, and... the loop breaks. The agent session is gone. Context is lost. Someone has to start over.

**HiveMind closes that loop.** It turns AI coding from a manual, single-user, single-session tool into a production-grade parallel workforce:

| Without HiveMind | With HiveMind |
|---|---|
| One person prompts one agent at a time | Submit 10 tickets, 3 agents work in parallel |
| Agent edits your working tree, conflicts with your changes | Each task runs in an isolated K8s pod with a fresh workspace |
| Agent crashes, you re-prompt from scratch | Automatic retry with full context (merge conflicts, pipeline failures, review feedback) |
| You review the MR, paste feedback into a new agent session | Review comments trigger follow-up sessions automatically |
| Agent only works in one repo | One ticket spans multiple repos -- the LLM picks which ones |
| Context dies when the tab closes | Per-agent memory blocks survive across sessions |
| You stare at a terminal to monitor progress | Web dashboard with live logs, MR tracking, pipeline status |

**The result:** Your AI agents go from "helpful assistant for one developer" to "autonomous workforce for the entire team." Tickets come in from GitLab issues, GitHub issues, webhooks, or the API. Merge requests come out. Humans review. Agents iterate. Nobody babysits a terminal.

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

## What Makes This Different?

### 1. Autonomous, Not Interactive

The agent doesn't need you to hold its hand. Submit a ticket and walk away. It clones repos, analyzes the codebase with LeanKG, makes changes, runs tests, commits, pushes, creates the MR, and waits for review. You only get involved when you review the MR.

### 2. Built-In Feedback Loops

This is the key differentiator. Most AI coding tools are fire-and-forget. HiveMind agents listen for feedback:

- **VCS review comments** trigger automatic follow-up sessions
- **Pipeline failures** re-queue the ticket with "fix the tests" context
- **Merge conflicts** re-queue with "rebase on target branch" instructions
- Up to **3 automatic retries** with full context from previous attempts

### 3. Multi-Repo Awareness

One ticket often touches multiple repositories (frontend + backend + shared types). The LLM analyzes which repos are relevant and the agent works across all of them in a single session. One commit per repo, one MR per repo, one ticket.

### 4. Multi-VCS Support

Works with both **GitLab** and **GitHub** (including GitHub Enterprise Server). Set `VCS_PROVIDER=github` or `gitlab` and the orchestrator handles the rest — webhooks, MR creation, review tracking all work through the same abstraction.

### 5. Persistent Agent Memory

Agents aren't blank slates. Each agent carries memory blocks that survive across sessions:

- **Persona** — how the agent writes code (style, conventions)
- **Human preferences** — "use conventional commits", "no emojis in commit messages"
- **Project context** — tech stack, test commands, architecture decisions

These persist on the orchestrator and are injected into every pod.

### 6. Code Intelligence with LeanKG

Before the agent starts, the init container indexes every selected repo with LeanKG. This gives the agent semantic code search — it can find relevant functions, understand dependencies, and navigate the codebase without guessing.

### 7. Infrastructure, Not a Plugin

HiveMind doesn't run inside your IDE. It runs on your Kubernetes cluster. That means:

- **Parallelism** — N agents working simultaneously, not one at a time
- **Isolation** — each task gets a fresh workspace, no state leaks
- **Security** — non-root containers, network policies, RBAC, API auth
- **Observability** — structured JSON logs, Prometheus metrics, web dashboard, SSE events
- **Scalability** — add more agent slots by changing one config value
- **Team access** — anyone can submit tickets, anyone can review MRs

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
- `kubectl` configured
- Docker (for building images)
- GitLab or GitHub instance + Personal Access Token
- Ollama endpoint (or any OpenAI-compatible LLM API)

### 1. Configure

```bash
./setup.sh
```

Interactive script that collects config values and creates K8s secrets. Reads defaults from `.env` so you only enter values once.

### 2. Build & Deploy

```bash
./redeploy.sh              # patch bump + build + deploy
```

Handles the full pipeline: semver bump, Docker build, K8s secret update, image import to worker nodes, manifest apply, rollout wait, git tag.

| Flag | What it does |
|------|-------------|
| `--minor` | Bump minor version (0.x.0) |
| `--major` | Bump major version (x.0.0) |
| `--version 2.0.0` | Use explicit version |
| `--no-cache` | Clean build |
| `--skip-build` | Re-deploy current version without rebuild |
| `--docker` | Build via docker-compose (local testing) |
| `--tag` | Create git tag for current version |
| `--changelog` | Show changelog since last tag |

### 3. Open the Dashboard

```bash
kubectl port-forward -n hivemind deployment/orchestrator 8080:8080
```

Open [http://localhost:8080](http://localhost:8080) — dashboard with idle agent slots ready.

### 4. Verify Endpoints

```bash
curl http://localhost:8080/healthz          # {"status":"ok"}
curl http://localhost:8080/readyz           # {"status":"ok","repos_initialized":true}
curl http://localhost:8080/metrics          # Prometheus metrics
```

### 5. Submit Tickets

**Via Web UI** — Kanban board at `/tickets`

**Via API:**

```bash
curl -X POST http://localhost:8080/api/tickets \
  -H "Content-Type: application/json" \
  -d '{
    "id": "PROJ-123",
    "title": "Fix login error on mobile",
    "description": "Login button does not show validation errors on mobile viewport...",
    "labels": ["bug", "frontend"],
    "priority": "High"
  }'
```

**With API key (if configured):**

```bash
curl -X POST http://localhost:8080/api/tickets \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{ ... }'
```

**Via Webhook** — configure your VCS project to send Issue events:
- GitLab: `http://<orchestrator>/webhooks/gitlab`
- GitHub: `http://<orchestrator>/webhooks/github`

### 6. CLI Tool

```bash
./cli/hivemind ticket list
./cli/hivemind ticket create PROJ-123 "Fix login error"
./cli/hivemind agent list
./cli/hivemind repo list
./cli/hivemind stream                    # Live SSE event stream
./cli/hivemind version
```

### 7. Watch & Interact

```bash
# Agent pod logs
kubectl -n hivemind logs -f agent-worker-proj-123

# Or access the agent's live opencode session through the orchestrator proxy
# Open: http://localhost:8080/agent-session/PROJ-123/
```

The agent will: test → analyze the codebase → make changes → run tests → commit & push → create MR → wait for your review. When you comment on the MR, the agent picks it up automatically.

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
| `/api/repos` | GET/POST/PUT/DELETE | Repository management |
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