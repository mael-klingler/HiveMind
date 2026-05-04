# HiveMind Orchestrator

Vollständiger K8s-ready Orchestrator mit Web-UI, SQLite-Datenbank, Task-Queue und Agenten-Management.

## Features

- **Web UI**: Dark-Mode Dashboard mit Live-Updates (SSE)
- **Ticket-Erstellung**: Direkt im Browser oder via REST-API
- **Task-Queue**: Automatische Zuweisung freier Agenten zu Tickets
- **Agenten-Management**: Einstellbare maximale Agenten-Anzahl
- **Live-Monitoring**: Agent-Status, Fortschrittsbalken, Aktivitäts-Log
- **SQLite**: Alles lokal persistiert
- **K8s-Ready**: InitContainer für Repo-Pull, PVC für Repositories

## Schnellstart

```bash
# 1. Dependencies
pip install -r requirements.txt

# 2. Server starten
python server.py

# 3. Web-UI öffnen
open http://localhost:8080
```

## Web UI

Die UI zeigt:
- **Queue-Anzahl**: Wartende + aktive Tickets
- **Agenten**: Status (idle/running), Progress-Bar, aktuelles Ticket
- **Ticket-Formular**: Direkte Erstellung mit ID, Titel, Beschreibung, Labels
- **Aktivitäts-Log**: Chronologische Schritt-Aufzeichnung pro Agent/Ticket
- **Live-Updates**: Via Server-Sent Events (SSE) – keine manuelle Refresh nötig

## REST API

| Endpoint | Methode | Beschreibung |
|---|---|---|
| `/` | GET | Web UI (HTML) |
| `/api/stream` | GET | SSE Live-Stream |
| `/api/tickets` | GET | Alle Tickets |
| `/api/tickets` | POST | Ticket erstellen |
| `/api/agents` | GET | Alle Agenten |
| `/api/queue` | GET | Queue-Übersicht |
| `/api/steps` | GET | Aktivitäts-Log |
| `/api/config` | GET/POST | Einstellungen (z.B. max_agents) |
| `/api/agents/{id}/progress` | POST | Fortschritt melden |
| `/api/agents/{id}/complete` | POST | Ticket abschließen |

## Ticket erstellen (cURL)

```bash
curl -X POST http://localhost:8080/api/tickets \
  -H "Content-Type: application/json" \
  -d '{
    "id": "PROJ-123",
    "title": "Login-Button auf Mobile zeigt keine Fehlermeldungen",
    "description": "Wenn ein User auf der mobilen Ansicht...",
    "issue_type": "Bug",
    "priority": "High",
    "labels": ["mobile", "frontend", "bug"]
  }'
```

## Datenbank-Schema

- **tickets**: Ticket-Daten + Status
- **agents**: Agenten + aktuelles Ticket + Progress
- **queue**: Warteschlange mit Zuweisung
- **steps**: Einzelne Arbeitsschritte (Audit-Trail)
- **config**: Einstellungen (max_agents, etc.)

## Queue-Logik

```
1. Ticket wird erstellt → Status: queued
2. Ticket landet automatisch in `queue` Tabelle
3. Queue-Processor (Hintergrund-Task) prüft alle 2 Sekunden:
   - Gibt es freie Agenten? (status = idle)
   - Gibt es wartende Tickets? (status = waiting)
4. Zuweisung: Ticket → freier Agent
   - Agent.status = running
   - Ticket.status = running
   - Queue.status = running
5. Agent arbeitet → meldet progress via API
6. Agent fertig → Ticket & Queue completed
7. Automatisch: Nächstes Ticket aus Queue wird zugewiesen
```

## K8s Deployment

```bash
# Helm-Chart installieren
helm install orchestrator ./helm/orchestrator \
  --namespace hivemind \
  --create-namespace \
  --set env.gitToken="glpat-xxxxxxxxxxxxxxxxx"

# Port-Forward für Zugriff
kubectl port-forward -n hivemind svc/orchestrator 8080:8080
```

## Konfiguration via .env

```bash
GIT_HOST=https://gitlab.com
GIT_USER=gitlab-ci-token
GIT_TOKEN=glciy-xxxxxxxxxxxxxxxxx
OLLAMA_HOST=http://ollama:11434
```
