# HiveMind

Automated AI coding agents on Kubernetes. Ticket in -> Agent pod spawns -> Merge request out.

## Quick Start

```bash
# 1. Prepare cluster
kubectl create namespace hivemind
echo '{"id":"PROJ-123","title":"Test","description":"...","labels":["test"]}' > ticket.json

# 2. Deploy
helm upgrade --install orchestrator ./Orchestrator/helm/orchestrator \
  --namespace hivemind --set rbac.create=true

# 3. Submit ticket
kubectl -n hivemind exec deploy/orchestrator -- \
  python3 /app/main.py process /app/tickets/ticket.json

# 4. Follow
kubectl -n hivemind logs -f agent-worker-proj-123
```

## Architecture

```
Ticket -> Orchestrator (K8s Deployment)
              | kubectl apply
        ConfigMaps + Agent-Pod (restartPolicy: Never)
              |
        Init: git clone -> Main: opencode run -> git push -> Merge Request
```

## Documentation

| Document | Content |
|----------|---------|
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Architecture, components, deployment guide |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Detailed data flows, design decisions |
| [`docs/USAGE.md`](docs/USAGE.md) | Step-by-step: submit ticket, follow agent, review MR |
| [`docs/SECURITY.md`](docs/SECURITY.md) | RBAC, secrets, threat model, best practices |

## Components

| Folder | Description |
|--------|-------------|
| `Orchestrator/` | Python-3 ticket analysis + agent pod spawner |
| `Agent/` | Node.js opencode container + GitLab MR via @gitbeaker/rest |
| `docs/` | Documentation |
| `docker-compose.yaml` | Local test environment |

## Commands

```bash
# Orchestrator: init, update, process, serve
python3 Orchestrator/main.py process ticket.json

# Test agent (dry-run)
docker compose --profile run up agent
```