# HiveMind

**Autonomous AI coding agents on Kubernetes. Ticket in, merge request out.**

[![Version](https://img.shields.io/badge/version-0.1.26-blue)](.version)
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

**The result:** Your AI agents go from "helpful assistant for one developer" to "autonomous workforce for the entire team." Tickets come in from GitLab issues, webhooks, or the API. Merge requests come out. Humans review. Agents iterate. Nobody babysits a terminal.

---

## How It Works — The Full Loop

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │                         YOU SUBMIT A TICKET                          │
  │                    (GitLab issue / API / Web UI)                     │
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
  │   Phase 1: opencode run — analyzes codebase, makes changes           │
  │   Phase 2: git commit + push to feature/TICKET-ID branch             │
  │            creates GitLab Merge Request                              │
  │   Phase 3: polls for your review comments                            │
  │            runs follow-up sessions to address feedback               │
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
  │   watches MR state via GitLab API                                    │
  │   ├── pipeline failed?   → re-queue with "fix the pipeline" context  │
  │   ├── merge conflict?    → re-queue with "rebase and resolve"        │
  │   ├── review comments?   → agent runs follow-up, pushes fixes        │
  │   └── MR merged?         → mark completed, delete pod                │
  └──────────────────────────────────────────────────────────────────────┘
```

---

## What Makes This Different?

### 1. Autonomous, Not Interactive

The agent doesn't need you to hold its hand. Submit a ticket and walk away. It clones repos, analyzes the codebase with LeanKG, makes changes, commits, pushes, creates the MR, and waits for review. You only get involved when you review the MR.

### 2. Built-In Feedback Loops

This is the key differentiator. Most AI coding tools are fire-and-forget. HiveMind agents listen for feedback:

- **GitLab review comments** trigger automatic follow-up sessions
- **Pipeline failures** re-queue the ticket with "fix the tests" context
- **Merge conflicts** re-queue with "rebase on target branch" instructions
- Up to **3 automatic retries** with full context from previous attempts

### 3. Multi-Repo Awareness

One ticket often touches multiple repositories (frontend + backend + shared types). The LLM analyzes which repos are relevant and the agent works across all of them in a single session. One commit per repo, one MR per repo, one ticket.

### 4. Persistent Agent Memory

Agents aren't blank slates. Each agent carries memory blocks that survive across sessions:

- **Persona** — how the agent writes code (style, conventions)
- **Human preferences** — "use conventional commits", "no emojis in commit messages"
- **Project context** — tech stack, test commands, architecture decisions

These persist on the orchestrator and are injected into every pod.

### 5. Code Intelligence with LeanKG

Before the agent starts, the init container indexes every selected repo with LeanKG. This gives the agent semantic code search — it can find relevant functions, understand dependencies, and navigate the codebase without guessing.

### 6. Infrastructure, Not a Plugin

HiveMind doesn't run inside your IDE. It runs on your Kubernetes cluster. That means:

- **Parallelism** — N agents working simultaneously, not one at a time
- **Isolation** — each task gets a fresh workspace, no state leaks
- **Observability** — web dashboard, live logs, MR tracking, SSE events
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
| **Agent Session Proxy** | Forwards opencode web UI from agent pods through `/agent-session/{ticket_id}/` |
| **Web UI** | Dashboard, Kanban board, agent/repo management, settings — live SSE updates |
| **REST API** | Full CRUD for tickets, agents, queue, repos, settings, MCP servers, memory, plugins |
| **GitLab Webhooks** | Auto-create tickets from GitLab issues, track MR state changes |

### Agent (opencode-ai / Node.js 22)

Ephemeral K8s pods (`restartPolicy: Never`) that do the actual work:

| Phase | What happens |
|-------|-------------|
| **1 — Work** | Reads assignment, starts opencode web (port 4096), runs `opencode run` with the task |
| **2 — Ship** | Commits to `feature/{TICKET_ID}`, pushes, creates GitLab MR via `@gitbeaker/rest` |
| **3 — Listen** | Polls orchestrator for human review comments, runs follow-up sessions on feedback |

### Init Container

Runs **before** the agent starts: clones selected repos and runs `leankg init` + `leankg index` so the agent has full code knowledge from the first prompt.

---

## Kubernetes Architecture

HiveMind runs entirely on Kubernetes. No external dependencies beyond GitLab and an LLM endpoint.

### Deployed Resources

```
namespace/hivemind
serviceaccount/orchestrator
role/orchestrator-agent-manager          # pods, configmaps, services
rolebinding/orchestrator-agent-manager
secret/gitlab-agent-credentials          # SSH key + GitLab PAT
secret/ollama-cloud-api-key              # Optional: cloud LLM key
pvc/orchestrator-repos                   # 10Gi repo storage
deployment/orchestrator                  # 1 replica, init + main
service/orchestrator                     # ClusterIP :8080
service/agent-session                   # Headless — agent pod DNS for proxy
ingress/hivemind-orchestrator            # External access + WebSocket support
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
    app.kubernetes.io/component: agent   # Matched by agent-session headless service
spec:
  restartPolicy: Never
  volumes:
    - workspace                           # emptyDir: shared init ↔ main
    - repos-config                        # ConfigMap: repos.json
    - task-prompt                         # ConfigMap: assignment.md
    - opencode-config                     # ConfigMap: opencode.json
    - memory-blocks                       # ConfigMap: agent memory
  initContainers:
    - clone-repos                         # git clone + leankg index
  containers:
    - opencode-agent                      # entrypoint → opencode → push → MR → feedback
      ports: [4096]                       # opencode web (proxied by orchestrator)
      resources: { requests: 500m/1Gi, limits: 4/8Gi }
```

**Cleanup is automatic:**

| Scenario | What happens |
|----------|-------------|
| Pod succeeds | Ticket marked completed, pod deleted |
| Pod crashes | Ticket re-queued (up to 3 retries, 120s delay) |
| Pod goes stale (>60 min) | Auto-completed, pod cleaned up |
| MR merged | Pod deleted, agent slot freed |

### RBAC (Least Privilege)

- **Orchestrator** can manage pods, configmaps, and services in `hivemind` only
- **Agent pods** have zero K8s RBAC — they cannot access the API server, list pods, or read secrets

---

## Getting Started

### Prerequisites

- Kubernetes cluster (RKE2, k3s, Docker Desktop, minikube, etc.)
- `kubectl` configured
- Docker (for building images)
- GitLab instance + Personal Access Token
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

### 3. Open the Dashboard

```bash
kubectl port-forward -n hivemind deployment/orchestrator 8080:8080
```

Open [http://localhost:8080](http://localhost:8080) — dashboard with idle agent slots ready.

### 4. Submit Tickets

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

**Via GitLab Webhook** — configure your GitLab project to send Issue events to `http://<orchestrator>/webhooks/gitlab`. Tickets are auto-created from GitLab issues.

### 5. Watch & Interact

```bash
# Agent pod logs
kubectl -n hivemind logs -f agent-worker-proj-123

# Or access the agent's live opencode session through the orchestrator proxy
# Open: http://localhost:8080/agent-session/PROJ-123/
```

The agent will: analyze the codebase → make changes → commit & push → create MR → wait for your review. When you comment on the MR, the agent picks it up automatically.

---

## Configuration

All config flows through `.env` (single source of truth):

| Variable | Description | Example |
|----------|-------------|---------|
| `GITLAB_HOST` | GitLab hostname | `gitlab.com` |
| `GIT_USER` | Git username for HTTPS clone | `gitlab-ci-token` |
| `GITLAB_TOKEN` | GitLab Personal Access Token | `glpat-...` |
| `OLLAMA_HOST` | Ollama endpoint (no `/v1`) | `http://host.docker.internal:11434` |
| `OLLAMA_BASE_URL` | Ollama endpoint (with `/v1`) | `http://host.docker.internal:11434/v1` |
| `OLLAMA_MODEL` | LLM for orchestrator analysis | `glm-5.1:cloud` |
| `OPENCODE_MODEL` | LLM for the coding agent | `glm-5.1:cloud` |
| `OLLAMA_CLOUD_API_KEY` | Cloud LLM key (optional) | `sk-...` |
| `AGENT_NAMESPACE` | K8s namespace | `hivemind` |
| `AGENT_IMAGE` | Docker image for agent pods | `hivemind-opencode:latest` |
| `AGENT_MAX_RETRIES` | Max retry attempts | `3` |
| `AGENT_RETRY_DELAY` | Seconds between retries | `120` |

---

## REST API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/tickets` | GET/POST | List or create tickets |
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
| `/api/repos/{name}/branches` | GET | List repo branches from GitLab |
| `/api/config` | GET/POST | Max agent slots, version |
| `/api/settings` | GET/POST | Runtime settings |
| `/api/mcp-servers` | GET/POST/PATCH/DELETE | MCP server config |
| `/api/plugins` | GET/POST/PATCH/DELETE | opencode plugin config |
| `/api/stream` | GET | SSE live event stream |
| `/agent-session/{ticket_id}/` | ANY | Proxy to agent's opencode web UI |
| `/webhooks/gitlab` | POST | GitLab webhook receiver |

---

## Project Structure

```
.
├── Agent/                         # The autonomous coding agent
│   ├── Dockerfile                 # Multi-stage: LeanKG build + Node.js + opencode
│   ├── entrypoint.sh             # Agent lifecycle: setup → work → ship → listen
│   ├── opencode.json.template     # opencode config template (envsubst)
│   └── k8s/                       # Manifest templates for manual pod creation
│
├── Orchestrator/                  # The control plane
│   ├── main.py                    # CLI + core: repo manager, LLM client, pod spawner
│   ├── server.py                  # FastAPI: web UI, REST API, SSE, webhooks, monitors, proxy
│   ├── database.py                # SQLite: tickets, agents, queue, memory, repos
│   ├── Dockerfile                 # Multi-stage: LeanKG build + Python 3.11
│   ├── orchestrator_config.json   # Project config (repos, model, feature flags)
│   ├── requirements.txt           # fastapi + uvicorn + httpx + websockets
│   ├── static/                    # Dark-themed web UI
│   │   ├── index.html             # Dashboard
│   │   ├── tickets.html            # Kanban board with live logs + agent session links
│   │   ├── agent.html             # Agent profiles, skills, memory, MCP, instructions
│   │   ├── repos.html              # Repo management with branch dropdown from GitLab
│   │   └── settings.html           # Settings, pod status, orchestrator control
│   ├── k8s/                       # K8s manifests (namespaces, RBAC, services, ingress)
│   └── kustomize/                 # Kustomize overlays (base + dev/prod)
│
├── docs/                          # Architecture, deployment, usage, security docs
├── setup.sh                       # Interactive K8s setup (secrets, .env sync)
├── redeploy.sh                    # Versioned build + deploy (semver, image import, rollout)
├── test.sh                        # End-to-end test
├── docker-compose.yaml            # Local Docker Compose profiles
├── .env.example                   # Config template
└── .version                       # Current version (read by orchestrator + redeploy.sh)
```

---

## Documentation

| Document | Content |
|----------|---------|
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Architecture, components, deployment guide (Helm / Kubectl / Docker Compose) |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Data flows, design decisions, class diagrams |
| [`docs/USAGE.md`](docs/USAGE.md) | Submit ticket → follow agent → review MR → debug |
| [`docs/SECURITY.md`](docs/SECURITY.md) | RBAC, secrets, threat model, network policies |

---

## License

MIT