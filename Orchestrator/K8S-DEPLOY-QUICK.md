# Kubernetes Deployment – HiveMind Orchestrator

## Schnellstart (30s)

```bash
# 1. GitLab Token setzen (nur einmalig)
export GIT_TOKEN="glciy-xxxxxxxxxxxxxxx"

# 2. Deployen (Helm – empfohlen)
./deploy.sh helm dev

# 3. Port-Forward (oder Ingress nutzen)
./deploy.sh helm dev --pf
```

## Drei Deploy-Methoden

| Methode | Befehl | Wann nutzen |
|---------|--------|-------------|
| **Helm** (empfohlen) | `./deploy.sh helm dev` | Produktion, Updates |
| **Kustomize** | `./deploy.sh kustomize dev` | Multi-Env, GitOps |
| **Plain** | `./deploy.sh plain` | Quickstart, Minimal |

## Struktur

```
Orchestrator/
├── helm/orchestrator/         # Helm Chart
│   ├── Chart.yaml
│   ├── values.yaml            # Default-Werte (alle Repos auf development)
│   └── templates/
│       ├── deployment.yaml    # InitContainer + Orchestrator Container
│       ├── ingress.yaml       # TLS via cert-manager (optional)
│       ├── certificate.yaml   # cert-manager Certificate (optional)
│       ├── pvc.yaml           # PersistentVolumeClaim für Repos
│       ├── secret.yaml        # .env als Secret (.git-credentials)
│       ├── configmap.yaml     # orchestrator_config.json
│       ├── service.yaml       # ClusterIP
│       └── rbac.yaml          # ServiceAccount + Rechte
│
├── kustomize/                  # Kustomize – dev / prod Overlays
│   ├── base/
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   ├── pvc.yaml
│   │   ├── configmap.yaml     # Vorkonfigurierte Repos
│   │   └── kustomization.yaml
│   └── overlays/
│       ├── dev/
│       │   ├── configmap-patch.yaml   # Ollama Host: server.example.com
│       │   └── kustomization.yaml
│       └── prod/
│           ├── configmap-patch.yaml   # Ollama Host: server.example.prod
│           └── kustomization.yaml
│
├── k8s/                       # Plan K8s Manifeste (fallback)
│   └── orchestrator.yaml
│
├── deploy.sh                  # One-Command Deploy-Skript
├── K8S-DEPLOY.md              # Vollständige Dokumentation
└── .env.example               # Template für Secrets
```

## Variablen (alle in `.env` oder K8s Secrets)

```bash
GIT_HOST=YOUR_GITLAB_HOST          # GitLab Domain
GIT_USER=gitlab-ci-token                 # Default GitLab CI Benutzer
GIT_TOKEN=glciy-xxx                      # Personal Access Token

OLLAMA_HOST=http://localhost:11434       # Ollama Sidecar / Proxy
OLLAMA_MODEL=llm:latest            # LLM Modell

TRACK_BRANCH=development                  # Branch für alle Agenten-Workspaces
WORK_DIR=/app/workspace                  # Temporäre Workspaces
PVC_MOUNT_PATH=/app/workspace/repos      # Persistente Repos
```

## Zugriff

### Port-Forward (lokal)

```bash
kubectl port-forward -n hivemind svc/orchestrator 8080:8080
# → http://localhost:8080
```

### Ingress + TLS (Production)

```bash
# cert-manager + nginx-ingress voraussetzen
helm install orchestrator ./helm/orchestrator \
  --namespace hivemind \
  --set ingress.enabled=true \
  --set ingress.tls.enabled=true \
  --set ingress.tls.certManager.enabled=true \
  --set ingress.tls.certManager.issuerName=letsencrypt-prod

# → https://orchestrator.example.com
```

## Kommandos

```bash
# Status
kubectl get pods -n hivemind
kubectl get pvc -n hivemind
kubectl get ingress -n hivemind

# Logs
kubectl logs -n hivemind deploy/orchestrator -c repo-init   # Git Clone
kubectl logs -n hivemind deploy/orchestrator -f               # Server

# Neu starten (Force-Init aller Repos)
kubectl rollout restart deployment/orchestrator -n hivemind

# Scale
kubectl scale deployment orchestrator --replicas=2 -n hivemind
```

## Workflow

```
[User] → erstellt Ticket (Web-UI / API)
         → Ticket in Queue
         → Freier Agent zuweisen (development branch)
         → Agent kloned Repo
         → Agent arbeitet & comittet
         → PR/MR erstellt
         → Review (approved / changes_requested)
         → MR merged → Pod kann gelöscht werden
```
