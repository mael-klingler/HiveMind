# HiveMind Orchestrator – Kubernetes Deployment Guide

## Voraussetzungen

- Kubernetes Cluster (>= 1.24)
- `kubectl` installiert und mit Cluster verbunden
- `helm` (optional, aber empfohlen)
- Docker Image `example/orchestrator:latest` (oder Registry anpassen)

---

## Option 1: Helm Chart (Empfohlen)

### 1. Namespace erstellen

```bash
kubectl create namespace hivemind
```

### 2. GitLab Token als Secret erzeugen

Erstelle ein Personal Access Token in GitLab (Settings → Access Tokens):
- **Scope**: `read_repository`, `write_repository`

```bash
kubectl create secret generic orchestrator-git-token \
  --from-literal=GIT_TOKEN="glciy-xxxxxxxxxxxxxxxxxxxxxxx" \
  --namespace hivemind
```

### 3. Helm Chart deployen

```bash
helm install orchestrator ./helm/orchestrator \
  --namespace hivemind \
  --set image.repository="example/orchestrator" \
  --set config.ollama_host="http://server.example.com:11434" \
  --set config.repositories[0].branch="development"
```

### 4. Status prüfen

```bash
kubectl get pods -n hivemind -w
kubectl logs -n hivemind deploy/orchestrator -c repo-init
```

---

## Option 2: Plain K8s Manifeste

```bash
kubectl apply -f k8s/
```

---

## Zugriff auf Web-UI

### Port-Forward (lokal)

```bash
kubectl port-forward -n hivemind svc/orchestrator 8080:8080
# → http://localhost:8080
```

### Ingress (optional)

Füge zu `values.yaml` hinzu:

```yaml
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: orchestrator.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: orchestrator-tls
      hosts:
        - orchestrator.example.com
```

---

## Architektur im Cluster

```
Namespace: hivemind

Pod: orchestrator-xxx
├─ initContainer: repo-init
│  ├─ Cloned development branches
│  └─ leankg init + index
└─ Container: orchestrator
   ├─ FastAPI Server (Port 8080)
   ├─ Queue-Processor
   └─ SQLite DB

Volumes:
├─ repo-storage (PVC)  → /app/workspace/repos (persistent)
├─ workspace (emptyDir) → /app/workspace (temp)
└─ config (ConfigMap)  → /app/config

Secrets:
└─ orchestrator-env  → /app/config/.env
```

---

## Wartung

### Pod neustarten (Force-Init)

```bash
kubectl rollout restart deployment/orchestrator -n hivemind
```

### Logs

```bash
# Init-Logs (Git Clone)
kubectl logs -n hivemind deploy/orchestrator -c repo-init

# Server-Logs
kubectl logs -n hivemind deploy/orchestrator -f
```

### Neue Repos hinzufügen

Bearbeite `values.yaml` (oder ConfigMap) und redeployen:

```bash
helm upgrade orchestrator ./helm/orchestrator \
  --namespace hivemind \
  --reuse-values
```

---

## Troubleshooting

| Problem | Lösung |
|---------|--------|
| `ImagePullBackOff` | Image tag / Registry prüfen |
| `Pending` PVC | StorageClass verfügbar? |
| `Init:Error` (repo-init) | Git-Token prüfen, Logs lesen |
| `CrashLoopBackOff` | `kubectl logs` im Server-Container |
| Ollama nicht erreichbar | Port-Forward oder Service-URL |
