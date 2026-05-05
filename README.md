# HiveMind

Autonomous AI coding agents on Kubernetes. Ticket in, merge request out.

Local coding agents (Cursor, Copilot, opencode) sit inside one person's editor and vanish when the tab closes. HiveMind gives them an infrastructure: a queue, an orchestrator, ephemeral pods, and a review lifecycle -- so agents run unsupervised, in parallel, across multiple repositories, with built-in feedback loops and zero state leakage between tasks.

---

## The Problem with Local Coding Agents

| Limitation | What happens | What HiveMind does |
|---|---|---|
| **Single-user** | Only the person at the keyboard can steer the agent. | Agents pick tickets from a shared queue. Anyone on the team submits work. |
| **No isolation** | Agent edits your working tree, conflicts with your uncommitted changes. | Each task gets its own ephemeral K8s pod with a fresh workspace. No state leaks between tasks. |
| **No queue** | You run one agent session at a time, manually. | Orchestrator manages a work queue with N parallel agent slots. |
| **No retry** | Agent fails, you re-prompt by hand. | Failed pods are detected, tickets are re-queued with retry context (merge conflicts, pipeline failures, review feedback). Up to 3 automatic retries. |
| **No review loop** | Agent pushes a branch, you review later, manually tell it what to fix. | MR review comments trigger automatic follow-up sessions. The agent reads feedback, makes changes, commits, and pushes. |
| **Single repo** | Most agents operate in one repo at a time. | One ticket can span multiple repos -- the LLM picks which ones, the agent works across all of them. |
| **No observability** | You watch the terminal. Walk away, you lose context. | Web dashboard, live SSE events, per-ticket logs, MR tracking, pipeline status -- all in the browser. |
| **No persistence** | Agent learning dies with the session. | Per-agent memory blocks (persona, preferences, project conventions) survive across sessions. LeanKG code indexes persist on PVC. |

---

## How It Works

```
  Ticket ──▸ Orchestrator (K8s Deployment, port 8080)
                   │
                   ├── LLM analyzes ticket + repo context (Ollama)
                   ├── Selects 1-4 relevant repositories
                   ├── Generates assignment prompt
                   │
                   ├── kubectl apply ──────────────────────────────┐
                   │   ConfigMaps: repos, assignment,              │
                   │              opencode.json, memory            │
                   │   Secret: gitlab-agent-credentials            │
                   │                                               │  Agent Pod
                   │                                               │  (restartPolicy: Never)
                   │   Init Container ◄────────────────────────────┤
                   │     git clone + leankg index                  │
                   │                                               │
                   │   Main Container ◄────────────────────────────┤
                   │     opencode web (port 4096)                  │
                   │     opencode run (task prompt)                │
                   │     git commit + push                         │
                   │     create GitLab Merge Request ────────┐     │
                   │                                         │     │
                   │     comment polling ◄───────────────────┤     │
                   │       (wait for human feedback)         │     │
                   │       follow-up opencode run ───────────┤     │
                   │                                         │     │
                   ├── Review Lifecycle Monitor              │     │
                   │     watches MR state via GitLab API ◄───┘     │
                   │     pipeline failure → re-queue               │
                   │     merge conflict → re-queue                 │
                   │     MR merged → mark completed, delete pod    │
                   │                                               │
                   └── Agent Pod Monitor                           │
                         pod failed → re-queue (with delay)        │
                         pod succeeded → completed                 │
                         pod stale (>60 min) → auto-complete       │
```

### Key Components

**Orchestrator** -- FastAPI server (Python 3.11) running as a K8s Deployment:

- **Queue Processor**: Assigns idle agents to queued tickets, with smart agent selection based on repository affinity
- **Workspace Builder**: LLM-powered ticket analysis via Ollama, selects relevant repos, generates assignment prompts, spawns agent pods
- **Review Lifecycle Monitor**: Watches MRs for pipeline failures, merge conflicts, and review comments -- re-queues tickets with full retry context
- **Agent Pod Monitor**: Tracks pod health, handles failures with configurable retry (default: 3 retries, 120s delay), auto-cleanup
- **Web UI**: Dashboard, Kanban board, agent management, repo management, settings -- all with live SSE updates
- **REST API**: Full CRUD for tickets, agents, queue, repos, settings, MCP servers, agent instructions, memory blocks, plugins

**Agent** -- opencode-ai (Node.js 22) running in ephemeral K8s Pods:

- **Phase 1**: Reads assignment prompt, starts opencode web server (port 4096 for interactive monitoring), runs `opencode run` with the task
- **Phase 2**: Commits, pushes to `feature/{TICKET_ID}` branch, creates GitLab merge request via `@gitbeaker/rest`
- **Phase 3**: Polls for human review comments, runs follow-up opencode sessions to address feedback
- **Memory**: Per-agent persistent memory blocks (persona, human preferences, project conventions), agent journaling
- **Code Intelligence**: LeanKG indexes repos for semantic code search, accessible to the agent via MCP

**Init Container** -- Clones selected repos and runs `leankg init` + `leankg index` before the main agent container starts, so the agent has full code knowledge from the first prompt.

---

## Kubernetes Architecture

HiveMind runs entirely on Kubernetes -- no external dependencies beyond your GitLab and LLM endpoint.

### Namespace: `hivemind`

All resources live in the `hivemind` namespace. The orchestrator Deployment is the only long-running component. Agent Pods are ephemeral (`restartPolicy: Never`) and are created/deleted dynamically.

### Persistent Storage

| PVC | Purpose |
|-----|---------|
| `orchestrator-repos` | Cloned git repositories (shared between orchestrator and agent init containers) |
| `orchestrator-db` | SQLite database (tickets, agents, queue, settings, memory blocks) |

### Secrets

| Secret | Contents |
|--------|----------|
| `orchestrator-env` | All runtime config: `GITLAB_TOKEN`, `OLLAMA_HOST`, `OLLAMA_MODEL`, `OPENCODE_MODEL`, `AGENT_IMAGE`, etc. |
| `gitlab-agent-credentials` | SSH private key + GitLab PAT for agent pods (clone + push) |
| `ollama-cloud-api-key` | Optional: Ollama Cloud API key for agent pods |

### Deployed Resources

```
namespace/hivemind
serviceaccount/orchestrator
role/orchestrator-agent-manager          # create/delete pods, configmaps
rolebinding/orchestrator-agent-manager
configmap/orchestrator-config             # orchestrator_config.json
secret/orchestrator-env                  # all env vars from .env
secret/gitlab-agent-credentials          # ssh key + gitlab token
pvc/orchestrator-repos                   # 10Gi repo storage
pvc/orchestrator-db                      # 1Gi database
deployment/orchestrator                  # 1 replica, init container + main
service/orchestrator                     # ClusterIP :8080
```

### Agent Pod Lifecycle

Each ticket spawns a Pod with this structure:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: agent-worker-{ticket-id}
  namespace: hivemind
spec:
  restartPolicy: Never                    # Ephemeral -- runs once, then terminates
  volumes:
    - name: workspace                     # emptyDir: shared between init + main
    - name: repos-config                  # ConfigMap: repos.json
    - name: task-prompt                   # ConfigMap: assignment.md
    - name: opencode-config               # ConfigMap: opencode.json
    - name: memory-blocks                 # ConfigMap: agent memory (persona, etc.)
  initContainers:
    - name: clone-repos                   # git clone + leankg init/index each repo
  containers:
    - name: opencode-agent               # entrypoint.sh → opencode run → push → MR
      ports:
        - containerPort: 4096            # opencode web server (interactive)
      resources:
        requests: { cpu: 500m, memory: 1Gi }
        limits:   { cpu: 4,    memory: 8Gi }
```

Pods are cleaned up automatically:
- **Success**: Pod terminates, orchestrator marks ticket as completed, deletes pod
- **Failure**: Pod crashes, orchestrator re-queues the ticket (up to 3 retries with 120s delay)
- **Stale**: Pod disappears without terminating (>60 min), orchestrator auto-completes and cleans up

### RBAC (Least Privilege)

The orchestrator ServiceAccount can only:
- `create/update/patch/delete/get/list` ConfigMaps and Pods in the `hivemind` namespace
- `get/list` Pod logs

Agent pods have **zero** K8s RBAC -- they cannot access the API server, cannot list pods, cannot read secrets beyond what is mounted into their filesystem.

### Kustomize Overlays

```
Orchestrator/kustomize/
  base/                    # Production-ready base manifests
    namespace.yaml
    pvc.yaml
    configmap.yaml
    rbac.yaml
    deployment.yaml
    service.yaml
    kustomization.yaml
  overlays/
    dev/                   # Dev overlay: local config, ingress
      kustomization.yaml
      configmap-patch.yaml
      ingress.yaml
    prod/                  # Prod overlay: production config
      kustomization.yaml
      configmap-patch.yaml
```

---

## Getting Started

### Prerequisites

- A Kubernetes cluster (RKE2, k3s, Docker Desktop, minikube, etc.)
- `kubectl` configured and pointing at your cluster
- Docker (for building images)
- A GitLab instance with a Personal Access Token
- An Ollama endpoint (or any OpenAI-compatible LLM API)

### 1. Configure -- `setup.sh`

`setup.sh` is an interactive script that collects all configuration values and stores them as a K8s Secret. It reads defaults from `.env` (or copies `.env.example` if `.env` doesn't exist) so you only enter values once.

```bash
./setup.sh
```

What it does:

1. **Checks prerequisites** -- `kubectl` installed, cluster reachable
2. **Creates the `hivemind` namespace** if it doesn't exist
3. **Prompts for configuration values** with defaults from `.env`:
   - `GITLAB_HOST` -- your GitLab instance (e.g. `gitlab.com`)
   - `GIT_USER` -- git username (default: `gitlab-ci-token`)
   - `GITLAB_TOKEN` -- Personal Access Token (hidden input)
   - `OLLAMA_HOST` -- Ollama endpoint without `/v1` (e.g. `http://host.docker.internal:11434`)
   - `OLLAMA_BASE_URL` -- Ollama endpoint with `/v1` (e.g. `http://host.docker.internal:11434/v1`)
   - `OLLAMA_MODEL` -- model name (e.g. `glm-5.1:cloud`)
   - `OPENCODE_MODEL` -- same model name for the agent
   - `OLLAMA_CLOUD_API_KEY` -- optional cloud API key (hidden input)
   - `AGENT_IMAGE` -- Docker image tag for agent pods
4. **Creates the `orchestrator-env` K8s Secret** with all values
5. **Syncs entered values back to `.env`** so subsequent runs use your answers as defaults

After setup, run `./redeploy.sh` to build and deploy.

### 2. Build & Deploy -- `redeploy.sh`

`redeploy.sh` is the single command for building, versioning, and deploying HiveMind. It handles the full pipeline: semver bump, Docker build, K8s secret update, image import, manifest apply, rollout wait, and git tagging.

```bash
./redeploy.sh              # patch bump + build + deploy
```

What it does, step by step:

**Version Management:**
- Reads current version from `.version`
- Bumps version (default: patch) or uses explicit `--version X.Y.Z`
- Writes new version to `.version`

**Docker Build:**
- Builds `hivemind-opencode` (Agent image) from `Agent/Dockerfile`
- Builds `hivemind-orchestrator` (Orchestrator image) from `Orchestrator/Dockerfile`
- Tags both images with the new version and `latest`
- With `--no-cache`: removes all old HiveMind images and prunes build cache first

**K8s Deploy:**
- Updates `kustomization.yaml` and `deployment.yaml` with the new image tag
- Reads `.env` and patches the `orchestrator-env` K8s Secret (so agents always use the latest config)
- Force-deletes the old orchestrator Deployment and Pods
- **Imports images into worker nodes** -- `docker save` creates a tarball, then pipes it into each worker node via `docker exec` and `ctr -n k8s.io images import`. This is what makes local images available on RKE2/k3s clusters that don't have a registry.
- Applies Kustomize manifests: `kubectl apply -k Orchestrator/kustomize/base/`
- Waits for rollout: `kubectl rollout status deployment/orchestrator --timeout=120s`
- Commits and tags the release in git

**Alternative modes:**

| Flag | What it does |
|------|-------------|
| `--minor` | Bump minor version (0.x.0) |
| `--major` | Bump major version (x.0.0) |
| `--version 2.0.0` | Use an explicit version |
| `--no-cache` | Clean build: remove all old images first |
| `--skip-build` | Only re-deploy the current version (no rebuild) |
| `--docker` | Build via docker-compose instead (local testing) |
| `--tag` | Create a git tag without deploying |
| `--changelog` | Show git log since the last tag |

### 3. Access the Web UI

```bash
kubectl port-forward -n hivemind deployment/orchestrator 8080:8080
```

Open [http://localhost:8080](http://localhost:8080) -- you'll see the dashboard with 3 idle agent slots.

### 4. Submit a Ticket

Via the Web UI (Kanban board), or via API:

```bash
curl -X POST http://localhost:8080/api/tickets \
  -H "Content-Type: application/json" \
  -d '{"id":"PROJ-123","title":"Fix login error display on mobile","description":"The login button on the mobile view does not show validation errors...","labels":["bug","frontend"],"priority":"High"}'
```

Or via GitLab webhook: configure your GitLab project to send Issue events to `http://<orchestrator>/api/webhooks/gitlab`.

### 5. Watch the Agent Work

```bash
# Follow agent pod logs
kubectl -n hivemind logs -f agent-worker-proj-123

# Or access the opencode web UI on the agent pod
kubectl port-forward -n hivemind agent-worker-proj-123 4096:4096
```

The agent will:
1. Analyze the codebase using LeanKG
2. Make code changes across relevant repos
3. Commit to `feature/PROJ-123` and push
4. Create a GitLab Merge Request
5. Wait for your review feedback

---

## Configuration

All configuration flows through `.env` (the single source of truth):

| Variable | Description | Example |
|----------|-------------|---------|
| `GITLAB_HOST` | GitLab instance hostname | `gitlab.com` |
| `GIT_USER` | Git username for HTTPS clone | `gitlab-ci-token` |
| `GITLAB_TOKEN` | GitLab Personal Access Token | `glpat-...` |
| `OLLAMA_HOST` | Ollama endpoint (no `/v1`) | `http://host.docker.internal:11434` |
| `OLLAMA_BASE_URL` | Ollama endpoint (with `/v1`) | `http://host.docker.internal:11434/v1` |
| `OLLAMA_MODEL` | LLM model for orchestrator analysis | `glm-5.1:cloud` |
| `OPENCODE_MODEL` | LLM model for the coding agent | `glm-5.1:cloud` |
| `OLLAMA_CLOUD_API_KEY` | Optional: cloud API key | `sk-...` |
| `AGENT_NAMESPACE` | K8s namespace | `hivemind` |
| `AGENT_IMAGE` | Docker image for agent pods | `hivemind-opencode:latest` |
| `DRY_RUN` | If true, agent prints task but does nothing | `false` |

---

## Documentation

| Document | Content |
|----------|---------|
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Architecture, components, deployment guide (Helm / Kubectl / Docker Compose) |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Detailed data flows, design decisions, class diagrams |
| [`docs/USAGE.md`](docs/USAGE.md) | Step-by-step: submit ticket, follow agent, review MR, debug |
| [`docs/SECURITY.md`](docs/SECURITY.md) | RBAC, secrets, threat model, network policies, best practices |

---

## Project Structure

```
.
├── Agent/                         # The autonomous coding agent
│   ├── Dockerfile                 # Multi-stage: LeanKG build + Node.js runtime + opencode
│   ├── entrypoint.sh              # Agent lifecycle: setup → opencode run → commit/push/MR → feedback loop
│   ├── opencode.json.template     # opencode AI config template (envsubst)
│   ├── repos.json                 # Fallback repo mapping
│   └── k8s/                       # K8s manifest templates for manual agent pod creation
│
├── Orchestrator/                  # The control plane
│   ├── main.py                    # CLI + core logic (repo manager, LLM client, pod spawner)
│   ├── server.py                  # FastAPI: web UI, REST API, SSE, GitLab webhooks, monitors
│   ├── database.py                # SQLite persistence (tickets, agents, queue, memory blocks)
│   ├── Dockerfile                 # Multi-stage: LeanKG build + Python 3.11 runtime
│   ├── orchestrator_config.json   # Project config (repos, model, feature flags)
│   ├── requirements.txt           # fastapi + uvicorn
│   ├── static/                    # Dark-themed web UI (dashboard, kanban, agents, repos, settings)
│   └── kustomize/                 # K8s manifests (base + dev/prod overlays)
│
├── docs/                          # Architecture, deployment, usage, security docs
├── setup.sh                       # Interactive K8s setup (secrets, .env sync)
├── redeploy.sh                    # Versioned build + deploy (semver, image import, rollout)
├── test.sh                        # End-to-end test (dry-run or live)
├── docker-compose.yaml            # Local Docker Compose (profiles: init, process, run, full, tickets)
├── .env.example                   # Single source of truth for all config values
└── .version                       # Current version (read by redeploy.sh)
```

---

## License

MIT