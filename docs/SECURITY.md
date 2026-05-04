# Sicherheitsdokumentation

Sicherheit ist ein fundamentales Design-Prinzip von HiveMind.

---

## 1. Architektur-Prinzipien

### 1.1 Least Privilege

Jede Komponente hat nur die absolut nötigen Rechte.

- **Orchestrator**: Nur ConfigMap + Pod CRUD im `hivemind` Namespace
- **Agent**: Kein K8s-Zugriff, nur filesystem + Netzwerk
- **GitLab**: Deduizierter Agent-Account (kein Admin/Owner)

### 1.2 Ephemeral Execution

- Agent-Pod startet → bearbeitet → terminiert
- Keine Idle-Ressourcen, keine persistenten Pods
- `restartPolicy: Never`

### 1.3 Credential Isolation

| Secret | Scope | Nutzer |
|--------|-------|--------|
| GitLab SSH Key | Agent-Pod (init clone + git push) | Agent |
| GitLab PAT | Agent-Pod (@gitbeaker/rest MR-Erstellung) | Agent |
| GitLab PAT | Orchestrator (falls HTTPS clone) | Orchestrator |

Kein Secret ist in Container-Images oder ConfigMaps embedded.

---

## 2. Kubernetes RBAC

### ServiceAccount: orchestrator
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: orchestrator
  namespace: hivemind
```

### Role: orchestrator-agent-manager
```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: orchestrator-agent-manager
  namespace: hivemind
rules:
  - apiGroups: [""]
    resources: ["configmaps"]
    verbs: ["create", "update", "patch", "delete", "get", "list"]
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["create", "update", "patch", "delete", "get", "list", "watch"]
  - apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get", "list"]
```

### ClusterRoleBinding: Namespace-Reader
```yaml
# Der Orchestrator muss checken ob Namespace existiert
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: orchestrator-namespace-reader
subjects:
  - kind: ServiceAccount
    name: orchestrator
    namespace: hivemind
roleRef:
  kind: ClusterRole
  name: view
  apiGroup: rbac.authorization.k8s.io
```

---

## 3. Secrets Management

### GitLab-Agent Secret
```bash
kubectl create secret generic gitlab-agent-credentials \
  --from-file=ssh-privatekey=/path/to/agent_id_rsa \
  --from-literal=GITLAB_TOKEN=glpat-xxxxxxxx \
  -n hivemind
```

**Empfohlene Token-Berechtigungen:**
- `api` (Lesen + Schreiben)
- `read_repository` (ggf. redundant wenn via SSH)
- `write_repository` (nur wenn via HTTPS)
- **Kein** `sudo`, `admin_area_access`

### Orchestrator Env Secret
```bash
kubectl create secret generic orchestrator-env \
  --from-literal=GITLAB_TOKEN=glpat-xxxxxxxx \
  --from-literal=GITLAB_HOST=gitlab.example.com \
  --from-literal=OPENCODE_MODEL=opencode-go/deepseek-v4-pro \
  -n hivemind
```

---

## 4. Git-Zugriff

### SSH statt HTTPS

| Modus | Vorteil | Nachteil |
|-------|---------|----------|
| **SSH** | Kein Token in Git-URL, SSH-Key per Secret | Key-Pair Management |
| HTTPS | Einfacher (Token in URL) | Token in Config/RAM |

**Empfohlen:** SSH mit eigenem Agent-Account.

### SSH-Key im Container
```bash
# Init Container mountet Secret
volumeMounts:
  - name: agent-ssh
    mountPath: /root/.ssh

# SSH Config (Dockerfile)
cat <<'EOF' > /root/.ssh/config
Host *.example.com
  StrictHostKeyChecking accept-new
  UserKnownHostsFile /root/.ssh/known_hosts
EOF

# SSH Command (Env im Pod)
GIT_SSH_COMMAND="ssh -i /root/.ssh/ssh-privatekey -o IdentitiesOnly=yes"
```

---

## 5. Opencode Security

### Permissions
```json
{
  "$schema": "https://opencode.ai/config.json",
  "permission": { "*": "allow" }
}
```

**Warnung:** `--dangerously-skip-permissions` heisst genau das – der Agent kann `bash`, `edit`, `read` alles im Workspace ausführen. Im Container ist das akzeptabel, auf der Host-Maschine jedoch nicht.

**Warum im Container OK ist:**
 - Pod hat keinen K8s-Zugriff
 - Nur gemountete Workspace-Volume ist beschreibbar
 - Netzwerk: Keine exponierten Ports

**Nicht empfohlen** für produktive Codebases ohne Review-Prozess.

---

## 6. Netzwerk

### Agent-Pod
- **Eingehend:** Keine Ports exponiert (kein Service)
- **Ausgehend:** Nur GitLab, Ollama (optional), Registry
- **NetworkPolicy** kann zusätzlich einschränken:
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: agent-deny-all
  namespace: hivemind
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/component: agent
  policyTypes:
    - Ingress
    - Egress
  ingress: []
  egress:
    - to:
        - namespaceSelector: {}
          podSelector:
            matchLabels:
              k8s-app: kube-dns
      ports:
        - protocol: UDP
          port: 53
    - to:
        - ipBlock:
            cidr: GITLAB_IP/32
      ports:
        - protocol: TCP
          port: 22   # SSH
        - protocol: TCP
          port: 443 # HTTPS API
```

---

## 7. Audit & Observability

### Logs
- Alle Logs landen auf stdout (keine Dateien im Pod)
- Kubernetes sammelt Logs über Node-Log-Driver
- `kubectl logs -n hivemind -l app.kubernetes.io/component=agent`

### Events
```bash
kubectl -n hivemind get events --sort-by='.lastTimestamp'
```

### Pod Labels
Alle Agent-Pods haben Labels für Monitoring:
```yaml
labels:
  app.kubernetes.io/name: hivemind
  app.kubernetes.io/component: agent
  ticket-id: "PROJ-123"
```

---

## 8. Best Practices für Produktion

1. **Private Registry**: Images nicht auf Docker Hub public
2. **Image Scanning**: Trivy/Clair vor Deployment
3. **Resource Limits**: CPU/Memory Limits auf Pod-Ebene
4. **Pod Security Standards**: Restricted PSP / OPA Gatekeeper
5. **Secret Rotation**: GitLab Token alle 90 Tage neu generieren
6. **MR-Protection**: `CODEOWNERS` + `MR-Approval` Policy in GitLab
7. **Backup**: Orchestrator-PVC regelmäßig backupen (Repos)
8. **Monitoring**: Prometheus metrics für Pod-Dauer, Erfolgsrate

---

## 9. Threat Model

| Bedrohung | Schwere | Mitigation |
|-----------|---------|------------|
| Agent schreibt in falsches Repo | Hoch | Dedicated Agent-Account, kein Owner | 
| Agent exfiltriert Code | Mittel | NetworkPolicy, keine Internet-Egress |
| Token-Leak | Hoch | Secret-Mount, kein Hardcoding | 
| LLM-Halluzinationen | Mittel | MR-Review-Prozess erforderlich | 
| DDoS via viele Tickets | Mittel | Rate-Limiting im Orchestrator |
| Container-Escape | Tief | PSP/Restricted, non-root (zukünftig) | 

