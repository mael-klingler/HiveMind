# Architektur-Dokumentation

Detaillierte Beschreibung aller Komponenten, Datenflüsse und Entscheidungen.

---

## Datenfluss: Ticket → MR

```
Ticket (JSON) → Orchestrator → LLM-Analyse → Selected Repos
                                              ↓
                                        ConfigMaps (repos.json, assignment.md)
                                              ↓
                                        kubectl apply → Agent Pod
                                              ↓
                                        Init: git clone
                                        Main: opencode run
                                              ↓
                                        git commit / push
                                              ↓
                                        @gitbeaker/rest → GitLab MR
```

---

## 1. Orchestrator

### Klassen-Struktur

| Klasse | Zweck |
|--------|-------|
| `RepoConfig` | Name, URL, Branch, Description, Tags eines Repos |
| `OrchestratorConfig` | Gesamtkonfig aus `orchestrator_config.json` |
| `RepoStatus` | Zustand eines Repos (last_commit, remote_commit, changes, error) |
| `Ticket` | Parsed aus JSON: id, title, description, labels, ... |
| `Logger` | Formatierte Ausgabe mit Emoji-Markern |
| `RepoManager` | Git clone, fetch, checkout, Status-Tracking |
| `LeanKGManager` | Code-Indizierung, semantische Suche |
| `OllamaClient` | LLM-Analyse via `/api/generate` (JSON output) |
| `Orchestrator` | Koordiniert Init, Update, Process, Agent-Spawning |

### `process()` Methode (Schritt für Schritt)

```python
def process(self, ticket, use_llm, skip_clone):
    1. self.update()              # Alle Repos fetchen
    2. LLM.repo_auswahl()         # Heuristik/LLM → selected repos
    3. generate_prompt()          # Markdown-Prompt bauen
    4. Workspace bauen            # .opencode/{assignment,analysis,...}.json
    5. spawn_agent_pod()          # kubectl apply ConfigMaps + Pod
    6. return workspace_dir
```

### `spawn_agent_pod()` Details

```
1. NAMESPACE prüfen (sonst erstellen)
2. ConfigMap: agent-worker-<ID>-repos     ← {repo: url} JSON
3. ConfigMap: agent-worker-<ID>-assignment ← assignment.md
4. Pod YAML: Agent-Container-Definition
5. kubectl apply -f /tmp/agent-pod.yaml
6. Log-Ausgabe:
   → kubectl get pod ... -w (Status)
   → kubectl logs -f ... (Live-Output)
```

---

## 2. Agent

### Dockerfile-Layer

```
FROM node:22-slim
├── apt-get: git openssh-client ca-certificates curl jq
├── npm install -g @gitbeaker/rest  # GitLab API-Client
├── npm install -g opencode-ai      # opencode CLI
├── ssh-keyscan gitlab.com
├── ssh-keyscan YOUR_GITLAB_HOST
├── COPY opencode.json /etc/agent/
├── COPY entrypoint.sh /scripts/
└── ENTRYPOINT ["entrypoint.sh"]
```

### `@gitbeaker/rest` statt `glab`

`glab` CLI benötigt ca-certificates und hat auf ARM64 Build-Probleme. Stattdessen wurde eine `glab.js`-Wrapper-Skript in Node.js eingebaut, das direkt `@gitbeaker/rest` nutzt (Pure-JS, kein externer Download).

### entrypoint.sh Flowchart

```
1. TASK_FILE finden
   ├── /workspace/.opencode/assignment.md
   ├── /etc/agent/workspace/.opencode/assignment.md
   └── /etc/agent/task.md (Fallback)
2. TICKET_ID + TICKET_TITLE extrahieren
3. DRY_RUN prüfen
   └── true → nur Ausgabe, kein git/opencode
4. opencode run "$(cat task.md)" --dangerously-skip-permissions
5. Per Repo:
   ├── git status --porcelain → Änderungen?
   ├── git checkout -b feature/<ID>
   ├── git add -A && git commit && git push
   └── @gitbeaker/rest → Merge Request erstellen
6. Pod terminiert (restartPolicy: Never)
```

---

## 3. K8s RBAC

Der Orchestrator braucht im `hivemind` Namespace folgende Rechte:

| Ressource | Operations | Warum? |
|-----------|-----------|--------|
| ConfigMaps | create, update, patch, delete, get, list | Repos + Assignment-Daten |
| Pods | create, update, patch, delete, get, list, watch | Agent-Pod erstellen/löschen |
| Pods/log | get, list | Live-Logs verfolgen |

Umgesetzt als:
- **ServiceAccount** `orchestrator`
- **Role** `orchestrator-agent-manager` (Namespace-Scoped)
- **RoleBinding** SA → Role

---

## 4. Init Container im Agent-Pod

```bash
image: alpine:3.21
command:
  1. apk add git openssh-client jq bash
  2. cat repos.json
  3. for repo in keys: git clone url workspace/<repo>
```

**Warum Init Container?**
- Klare Trennung: Init macht Vorbereitung (clone), Main macht Arbeit (opencode)
- Falls clone fehlschlägt: Pod startet gar nicht erst
- Kein Volume-Mount zwischen Orchestrator und Agent nötig (nur ConfigMaps)

---

## 5. Prompt-Generierung

Der Assignment-Prompt enthält:

1. Ticket-Metadaten (ID, Typ, Priorität, Komplexität)
2. Zielbeschreibung
3. Volle Ticket-Beschreibung
4. LLM-Analyse + Begründung
5. Ausgewählte Repositories mit Beschreibung + Tags
6. Schritt-für-Schritt-Anweisungen
7. Akzeptanzkriterien (Checkliste)
8. Hinweise zu Conventions + Architektur

Beispiel: `Orchestrator/assignment_PROJ-123.md`

---

## 6. LeanKG Integration

Der Orchestrator nutzt LeanKG für:

1. **Repo-Indexierung**: `leankg index .` → `.leankg/` Ordner pro Repo
2. **Semantische Suche**: `leankg query <keywords>` → relevante Dateien
3. **Analyse-Kontext**: LLM bekommt top-5 relevante Dateien pro Repo als Input

Vorteil: Bessere Repo-Auswahl durch LLM (nicht nur Tag-Matching, sondern Code-Inhalte).

---

## 7. Ollama-Integration

Der Orchestrator spricht Ollama über HTTP:

```
POST http://ollama:11434/api/generate
{
  "model": "gemma4:26b",
  "system": "Du bist ein Software-Architekt...",
  "prompt": "<ticket>\n<repo-list>",
  "stream": false,
  "format": "json"
}
```

**Anforderung an JSON-Output**:
```json
{
  "selected_repos": ["gateway", "auth"],
  "complexity": "High",
  "estimated_hours": 8,
  "reasoning": "API + Auth betroffen...",
  "tech_stack": ["Go", "React"],
  "suggested_files": {"gateway": ["src/routes.go"]}
}
```

Falls LLM kein valides JSON liefert: Heuristischer Fallback (Keyword-Matching auf Tags).

---

## 8. Design-Prinzipien

| Prinzip | Umsetzung |
|---------|-----------|
| Ephemeral Pods | `restartPolicy: Never` → Agent-Pod löscht sich selbst |
| Least Privilege | Orchestrator nur via RBAC, Agent kein K8s-Zugriff |
| Immutable ConfigMaps | Jedes Ticket bekommt eigene ConfigMaps |
| Git-Zugriff via SSH | Keine Passwörter in Umgebungsvariablen für Git |
| GitLab API via Token | Personal Access Token per Secret |
| Resilent Fallbacks | LLM nicht erreichbar → Heuristik, Init clone fehl → continue |
| Observability | Pod-Logs direkt auf stdout, Status via `kubectl get pods` |

