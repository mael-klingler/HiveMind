# Usage Guide

Wie man ein Ticket einreicht, den Agent laufen lässt und den Merge Request verfolgt.

---

## Voraussetzungen

- RKE2 Cluster läuft
- `kubectl` konfiguriert auf den Cluster
- GitLab Account mit:
  - SSH-Key (für git clone/push)
  - Personal Access Token mit `api`, `write_repository`

---

## Schritt 1: Cluster vorbereiten

### 1.1 Namespace erstellen
```bash
kubectl create namespace hivemind
```

### 1.2 GitLab Secret anlegen
```bash
kubectl create secret generic gitlab-agent-credentials \
  --from-file=ssh-privatekey=/home/user/.ssh/agent_id_rsa \
  --from-literal=GITLAB_TOKEN=glpat-xxxxxxxx \
  -n hivemind
```

### 1.3 Orchestrator ConfigMap anlegen
```bash
kubectl create configmap orchestrator-config \
  --from-file=orchestrator_config.json=Orchestrator/orchestrator_config.json \
  -n hivemind
```

### 1.4 Agent Image pushen (Registry)
```bash
docker build -t my-registry/hivemind-opencode:latest -f Agent/Dockerfile Agent/
docker push my-registry/hivemind-opencode:latest

docker build -t my-registry/hivemind-orchestrator:latest -f Orchestrator/Dockerfile Orchestrator/
docker push my-registry/hivemind-orchestrator:latest
```

---

## Schritt 2: Orchestrator deployen

### Option A: Helm Chart
```bash
helm upgrade --install orchestrator ./Orchestrator/helm/orchestrator \
  --namespace hivemind \
  --set image.repository=my-registry/hivemind-orchestrator \
  --set agentImage=my-registry/hivemind-opencode:latest \
  --set rbac.create=true \
  --wait
```

### Option B: Kubectl
```bash
# In k8s/orchestrator.yaml anpassen:
# - image: my-registry/hivemind-orchestrator:latest
# - AGENT_IMAGE: my-registry/hivemind-opencode:latest
kubectl apply -f Orchestrator/k8s/orchestrator.yaml
```

**Status checken:**
```bash
kubectl -n hivemind get deployments,pods
kubectl -n hivemind logs -f deploy/orchestrator
```

---

## Schritt 3: Ticket einreichen

### Ticket-Format
```json
{
  "id": "PROJ-456",
  "title": "Payment webhook handler hinzufügen",
  "description": "Neue Stripe-Webhook-URL muss im payment-service empfangen werden...",
  "labels": ["payment", "backend", "api"],
  "issue_type": "Feature",
  "priority": "High"
}
```

### Via kubectl exec
```bash
# Ticket als ConfigMap/Secret in den Pod kopieren
kubectl -n hivemind cp ticket.json orchestrator-xxx:/tmp/ticket.json

# Verarbeiten
kubectl -n hivemind exec deploy/orchestrator -- \
  python3 /app/main.py process /tmp/ticket.json
```

### Via lokal (wenn kubectl gegen Cluster)
```bash
python3 Orchestrator/main.py process ticket.json
```

**Output:**
```
📋 Ticket geladen: PROJ-456 – Payment webhook...
=== Repository Update ===
   ✅ gateway: up-to-date (a1b2c3d)
   ✅ payment-service: up-to-date (e4f5g6h)
=== Auswahl ===
   LLM-Auswahl: ['payment-service', 'gateway']
   Primär: payment-service
   Komplexität: Medium
=== Agent-Pod erzeugen ===
   ✅ ConfigMap repos angelegt
   ✅ ConfigMap assignment angelegt
   ✅ Agent-Pod agent-worker-proj-456 gestartet
   Ticket: PROJ-456 – Payment webhook...
   Komplexität: Medium | Primär: payment-service
   Repos (2): payment-service, gateway
   Pod-Status: kubectl -n hivemind get pod agent-worker-proj-456 -w
```

---

## Schritt 4: Agent verfolgen

### Live-Logs
```bash
kubectl -n hivemind logs -f agent-worker-proj-456
```

**Beispiel-Ausgabe:**
```
📋 Ticket:  PROJ-456 – Payment webhook handler hinzufügen
🌿 Branch:  feature/PROJ-456
🤖 Starte opencode mit Task...
Analyzing repository payment-service...
Identifying relevant files...
Writing webhook handler...
Creating tests...
✅ opencode abgeschlossen
📦 payment-service: Erstelle Branch, Commit, Push und MR...
🔗 MR erstellt für payment-service: https://${GITLAB_HOST}/.../-/merge_requests/42
📦 gateway: Keine Änderungen, überspringe.
🏁 Alle Repos verarbeitet.
```

### Status
```bash
# Laufende Agent-Pods
kubectl -n hivemind get pods -l app.kubernetes.io/component=agent

# History (fertige Pods)
kubectl -n hivemind get pods -l app.kubernetes.io/component=agent --show-kind=true

# Pod details
kubectl -n hivemind describe pod agent-worker-proj-456
```

---

## Schritt 5: MR reviewen

```bash
# In GitLab → https://${GITLAB_HOST}/<>/-/merge_requests

Der MR-Title: [PROJ-456] Payment webhook handler hinzufügen
Target: development
```

**MR-Beschreibung:**
```
## Zusammenfassung
Dieser MR wurde automatisch durch den HiveMind Agent erstellt.

### Ticket
- **ID:** PROJ-456
- **Titel:** Payment webhook handler hinzufügen

### Was wurde geändert?
#### payment-service
- Neue Datei: src/handlers/webhook.go
- Tests: test/handlers/webhook_test.go
```

### MR freigeben
```bash
# Wenn Tests durchlaufen: GitLab-UI oder glab CLI
glab mr merge --auto-merge --remove-source-branch 42
```

---

## Mehrere Tickets parallel

Jeder `process`-Call erzeugt einen eigenen Pod:

```bash
# Ticket A
kubectl exec -n hivemind deploy/orchestrator -- \
  python3 /app/main.py process /tmp/ticket-a.json

# Ticket B (parallel)
kubectl exec -n hivemind deploy/orchestrator -- \
  python3 /app/main.py process /tmp/ticket-b.json

# Ergebnis: 2 Pods laufen gleichzeitig
kubectl -n hivemind get pods
# NAME                     READY   STATUS    RESTARTS   AGE
# orchestrator-xxx         1/1     Running   0          1h
# agent-worker-ticket-a    1/1     Running   0          2m
# agent-worker-ticket-b    1/1     Running   0          1m
```

---

## Agent-Pod manuell debuggen

```bash
# In den Pod schauen
kubectl -n hivemind exec -it agent-worker-proj-456 -- /bin/sh

# Git-Status
cd /workspace/payment-service && git log --oneline

# opencode manuell testen
cd /workspace && opencode run "$(cat /etc/agent/task.md)"

# GitLab API testen
node -e "const {Gitlab} = require('@gitbeaker/rest'); ..."
```

---

## Cleanup (fertige Pods löschen)

```bash
# Einzelner Pod
kubectl -n hivemind delete pod agent-worker-proj-456

# Alle fertigen Agent-Pods
kubectl -n hivemind get pods -l app.kubernetes.io/component=agent
# → Nicht nötig, Pod löscht sich selbst (restartPolicy: Never)
# Kubernetes GC entfernt Success-Pods nach der TTL
```

---

## Test-Modus (lokal)

```bash
# .env erstellen
cp .env.example .env
# → DRY_RUN=true setzen (keine echten Änderungen)
# → GITLAB_TOKEN mit Test-Account

# Agent direkt testen (ohne Orchestrator)
cat > /tmp/test-ticket.md << 'EOF'
# Auftrag: Test-Ticket
## Ticket-Referenz
- **ID:** TEST-001
- **Typ:** Task
- **Priorität:** Medium
## Benötigte Repositories
  - template
EOF

docker run --rm \
  -e DRY_RUN=true \
  -v /tmp/test-ticket.md:/etc/agent/task.md \
  -v ~/.ssh:/root/.ssh:ro \
  -e GITLAB_TOKEN=fake \
  hivemind-opencode:latest
```
