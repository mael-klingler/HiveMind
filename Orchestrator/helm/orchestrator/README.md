# Orchestrator Helm Chart

## Voraussetzungen

1. Kubernetes Cluster (>= 1.24)
2. Helm 3
3. PVC StorageClass verfügbar
4. Git SSH-Key Secret erstellt

## Schnellstart

```bash
# 1. Git SSH-Key Secret erstellen
kubectl create secret generic git-ssh-key \
  --namespace hivemind \
  --from-file=id_rsa=/pfad/zu/deinem/private-key

# 2. Helm installieren
helm install orchestrator ./helm/orchestrator \
  --namespace hivemind \
  --create-namespace \
  --set config.ollama_host=http://ollama.hivemind.svc.cluster.local:11434 \
  --set gitSshKey.existingSecret=git-ssh-key

# 3. Verifizieren
kubectl get pods -n hivemind
kubectl logs -n hivemind deploy/orchestrator -c repo-init
```

## Wichtige Befehle

### Repository-Init (beim Pod-Start automatisch)
```bash
kubectl exec -n hivemind deploy/orchestrator -- python3 /app/main.py init
```

### Update aller Repos
```bash
kubectl exec -n hivemind deploy/orchestrator -- python3 /app/main.py update
```

### Ticket verarbeiten
```bash
kubectl cp ticket.json hivemind/deploy/orchestrator:/tmp/
kubectl exec -n hivemind deploy/orchestrator -- python3 /app/main.py process /tmp/ticket.json --llm
kubectl cp hivemind/deploy/orchestrator:/app/workspace/workspace_TICKET-123 ./workspace_TICKET-123
```

## Architektur

```
Pod
├── initContainer: repo-init
│   ├── git clone/pull aller Repos (PVC)
│   └── leankg index .
├── container: orchestrator
│   ├── HTTP Server (API / Status)
│   └── Workspace-Generierung bei Anfrage
└── Volumes
    ├── repo-storage (PVC)     → /app/workspace/repos
    ├── workspace  (emptyDir)  → /app/workspace
    ├── config     (ConfigMap) → /app/config
    └── git-ssh-key (Secret)  → /root/.ssh
```
