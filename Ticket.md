# Architektur-Befund HiveMind

Read-only Analyse des HiveMind-Go-Codebases. Keine Lösungsvorschläge, nur Befund.
Priorisierung: kritisch / hoch / mittel / niedrig. Referenzen als `Datei:Zeile`.

## Kritisch

| # | Befund | Ort |
|---|---|---|
| 1 | GitHub-Token als Plaintext-Env im Pod-Spec. `GITHUB_TOKEN` als Klartext, während `GITLAB_TOKEN` korrekt als `SecretKeyRef` injiziert wird. Über `kubectl describe pod` sichtbar. | `pod_builder.go:99, 133` |
| 2 | Keine Owner-References / Finalizer, manuelle GC. Weder Pod noch die 4 ConfigMaps pro Ticket haben OwnerRefs. Bei Orchestrator-Crash zwischen Pod-Delete und CM-Delete bleiben ConfigMaps als Müll. `Foreground`-Propagation nutzlos, da CMs keine Owner sind. | `client.go:311-320`, `pod_builder.go:259-264, 306-324` |
| 3 | Polling statt Watch/Informer, doppelter Work bei 2 Replikas. `AgentMonitor` pollt alle 10s `ListPods`. Bei `replicas: 2` pollt jeder Replika unabhängig → doppelte Status-Updates und doppelte Pod-Deletes ohne Claim/Lock. | `agent_monitor.go:70-129`, `deployment.yaml:9` |

## Hoch

| # | Befund | Ort |
|---|---|---|
| 4 | `RequeueTicket` legt keinen Queue-Eintrag an → Ticket nach Spawn-Fehler verloren. Der Claim hat den alten Eintrag gelöscht. Ticket ist danach nicht mehr in der Queue. Datenverlust-Pfad. | `database.go:301-315, 598`, `queue_processor.go:133-138` |
| 5 | Kein Retry-Backoff. `RequeueTicket` wird sofort aufgerufen; `AgentRetryDelay` (120s) wird nirgends angewendet → sofortiger Re-Spawn-Loop bis `AgentMaxRetries`. | `queue_processor.go:133-138`, `config.go:85` |
| 6 | Invertierte Abhängigkeit api→background. Handler-Schicht kennt Worker-Schicht; sollte Interface aus `repository` konsumieren. | `server.go:36, 54` |
| 7 | Gott-Paket `database` (1348 Zeilen, ~60 Methoden) mit toter Interface-Schicht. `repository/repo.go:33-51` wird von `database.DB` nicht implementiert. `pgxrepo` hat parallele Implementierung mit byteweise identischem SQL. Zwei konkurrierende Persistenz-Implementierungen, die auseinanderdriften. | `database.go:422-437` vs `pgxrepo/repo.go:37-52`, `repository/repo.go:33-51` |
| 8 | Keine RBAC-Manifeste. Agent-Pods mounten Default-SA-Token (kein `serviceAccountName`/`automountServiceAccountToken: false`) → Agent kann K8s-API erreichen. | `deploy/kustomize/`, `pod_builder.go:259-278` |
| 9 | Drei Konfig-Wahrheitsquellen (Env / .env / DB-Settings), nicht synchronisiert. DB-Settings (`allowedSettings` inkl. `gitlab_token`, `agent_image`) sind für die Orchestrator-Laufzeit tot, da `Load()` nur Env liest. | `config.go:79-134`, `database.go:620-655` |
| 10 | K8s-Test-Coverage minimal. Nur `BuildPodSpec` getestet (4 Tests). Kein Test für `SpawnAgentPod`, `CleanupAgentResources`, `AgentMonitor`. Kein `fake.NewSimpleClientset`, kein Mock. | `pod_builder_test.go` |
| 11 | OllamaCloudAPIKey als Plaintext in ConfigMap. `Authorization: Bearer <key>` wird in `opencode.json`-ConfigMap geschrieben → Klartext in etcd und via `kubectl get cm`. | `pod_builder.go:510-512, 316-319` |
| 12 | Doppelte Secret-Quelle GitLab. `Agent/k8s/secret.yaml` (gitlab-agent-credentials) vs. `EnsureSecrets` (gitlab-token). Nur eine aktiv, verwirrend. | `Agent/k8s/secret.yaml:4`, `client.go:274-278` |

## Mittel

| # | Befund | Ort |
|---|---|---|
| 13 | `httpServer`-Fehler → `os.Exit(1)` ohne Worker-Shutdown. Hartes Exit mit Race gegen Graceful-Path. | `main.go:183` |
| 14 | `wg.Wait()` ohne Timeout. Blockiert unbegrenzt, wenn Worker hängen → SIGKILL nach K8s-Grace. | `main.go:199` |
| 15 | Keine Leader-Election bei 2 Replikas. `AgentMonitor`, `ReviewMonitor`, `Planner` ohne DB-Lock → doppelter Background-Work. | `deployment.yaml:9`, `main.go:157-162` |
| 16 | Startup-Orphan-Recovery ohne Lock. Läuft in beiden Replikas parallel → doppelter Requeue. | `main.go:128-144` |
| 17 | Broadcaster-Subscriber beendet sich bei Redis-Fehler dauerhaft. `return` statt Resubscribe-Loop. | `broadcaster.go:77` |
| 18 | `CreateSecret` schluckt `AlreadyExists` still, kein Drift-Check. | `client.go:194-198, 258-268` |
| 19 | `CreateConfigMap` Upsert ohne ResourceVersion (kein CAS). Optimistisches-Konflikt-Gefahr bei konkurrierenden Updates. | `client.go:141-143, 149-163` |
| 20 | `completeAgentTask` nicht-transaktional. 4 separate Exec-Calls (LLM-Usage + LineStats + TicketStatus + AgentStatus + Pod-Delete). | `server.go:559-621` |
| 21 | `GetSetting` schluckt alle Scan-Fehler → `"", nil`. Silent-Error-Smell. | `database.go:620-627` |
| 22 | `WaitForPodDeletion` 30s vs `GracePeriodSeconds` 300s Widerspruch. Pod kann noch laufen, wenn Spawn weitergeht. | `pod_builder.go:340`, `client.go:93` |
| 23 | `Agent/Dockerfile` ungepinted `@latest`-Packages + `curl | sh`. Supply-Chain-Risiko, keine reproduzierbaren Builds. | `Agent/Dockerfile:17, 22` |
| 24 | `:latest`-Image-Tag + `PullAlways` → keine Rollbacks. | `pod_builder.go:85-86` |
| 25 | Doppelter SSE-Pfad (`ServeHTTP` vs `streamEvents`). | `broadcaster.go:151-177`, `server.go:1249-1277` |
| 26 | `LearningWorker` bekommt rohen `*pgxpool.Pool` zusätzlich zu `*database.DB`. Abstraktion wird durchbrochen. | `learning.go:17-21`, `main.go:153` |

## Niedrig

| # | Befund | Ort |
|---|---|---|
| 27 | Token in `~/.git-credentials` während Clone-Fenster auslesbar. | `pod_builder.go:393` |
| 28 | Redundante Stop-Mechanismen (`ctx` + `stopCh`) in allen Workern. | `queue_processor.go:58-71` etc. |
| 29 | `contains`/`jsonUnmarshal` Helper in `queue_processor.go` ungenutzt. | `queue_processor.go:242-253` |
| 30 | Statisches `Agent/k8s/pod.yaml` driftet von dynamischem Builder (Init-Image `alpine:3.21` vs `hivemind-opencode:latest`). | `Agent/k8s/pod.yaml:31`, `pod_builder.go:85` |
| 31 | Env-Helper dupliziert (`config.getEnv` vs `k8s.getEnv`). | `config.go:183`, `pod_builder.go:566` |

---

Hinweis: Befund ohne Lösungsansätze. Die krassesten Punkte sind #4 (Ticket-Verlust nach Spawn-Fehler), #1 (GitHub-Token Klartext), #3 (Polling + 2 Replikas = doppelte Deletes) und #7 (Gott-Paket database + tote Repository-Schicht).