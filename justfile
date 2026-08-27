# HiveMind — Task Runner (https://just.systems/)
# ============================================================
# Replaces: setup.sh, redeploy.sh, test.sh, Makefile.go
# Install:  cargo install just  OR  curl -fsSL https://just.systems/install.sh | bash
# Usage:    just --list

set dotenv-load := true
set positional-arguments := true

VERSION := if env_var_or_default("VERSION", "") != "" { env_var("VERSION") } else { trim(shell("cat .version")) }
NAMESPACE := "hivemind"
DEPLOYMENT := "orchestrator"
REGISTRY := env_var_or_default("REGISTRY", "reg.capturecore.org/capturecore")
GO_BINARY := "orchestrator"

# Default: show available recipes
default:
    @just --list

# ─── Go Development ──────────────────────────────────────────

# Build the Go orchestrator binary
build:
    go build -ldflags="-s -w" -o {{GO_BINARY}} ./cmd/orchestrator

# Run the orchestrator locally (requires DATABASE_URL)
run: build
    ./{{GO_BINARY}} -serve

# Run Go unit tests
test:
    go test ./internal/... -v -count=1

# Run Go unit tests with race detector
test-race:
    go test ./internal/... -v -count=1 -race

# Run vet + static analysis
lint:
    go vet ./cmd/... ./internal/...

# Tidy go.mod
tidy:
    go mod tidy

# Run database migrations locally
migrate:
    go run ./cmd/orchestrator -migrate

# Clean build artifacts
clean:
    rm -f {{GO_BINARY}}

# ─── CI Recipes ───────────────────────────────────────────────

# Full CI pipeline for Go (used by GitHub Actions / GitLab CI)
ci-go:
    @echo "==> Go vet"
    go vet ./cmd/... ./internal/...
    @echo "==> Go test"
    go test ./internal/... -count=1 -v -timeout 120s

# Run all CI locally
ci: ci-go

# ─── Docker Build ─────────────────────────────────────────────

# Build Go orchestrator Docker image
docker-build-go:
    docker build -f Dockerfile.go \
        -t {{REGISTRY}}/hivemind-orchestrator:{{VERSION}} \
        -t {{REGISTRY}}/hivemind-orchestrator:latest \
        .

# Build agent Docker image
docker-build-agent:
    docker build \
        -t {{REGISTRY}}/hivemind-opencode:{{VERSION}} \
        -t {{REGISTRY}}/hivemind-opencode:latest \
        -f Agent/Dockerfile Agent/

# Build all images
docker-build: docker-build-go docker-build-agent

# Push orchestrator image to registry
docker-push-go:
    docker push {{REGISTRY}}/hivemind-orchestrator:{{VERSION}}
    docker push {{REGISTRY}}/hivemind-orchestrator:latest

# Push agent image to registry
docker-push-agent:
    docker push {{REGISTRY}}/hivemind-opencode:{{VERSION}}
    docker push {{REGISTRY}}/hivemind-opencode:latest

# Push all images
docker-push: docker-push-go docker-push-agent

# Build + push all images
docker-release: docker-build docker-push

# ─── Version Management ───────────────────────────────────────

# Show current version
version:
    @echo "{{VERSION}}"

# Bump patch version (0.0.x)
version-patch:
    @just _bump-version patch

# Bump minor version (0.x.0)
version-minor:
    @just _bump-version minor

# Bump major version (x.0.0)
version-major:
    @just _bump-version major

# Set explicit version
version-set VER:
    @echo "{{VER}}" > .version
    @echo "Version set to {{VER}}"

# Create git tag for current version
version-tag:
    git tag -a "v{{VERSION}}" -m "Release v{{VERSION}}"
    @echo "Tagged: v{{VERSION}}"
    @echo "Push with: git push origin v{{VERSION}}"

# Show changelog since last tag
changelog:
    #!/usr/bin/env bash
    set -e
    PREV_TAG=$(git tag --sort=-v:refname | head -1 2>/dev/null || true)
    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  Changelog ${PREV_TAG:-v0.0.0} → v{{VERSION}}"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""
    if [ -n "$PREV_TAG" ]; then
        git log "${PREV_TAG}..HEAD" --pretty=format:"  %h  %s" --no-merges
    else
        git log --pretty=format:"  %h  %s" --no-merges -20
    fi
    echo ""

# ─── K8s Deploy ───────────────────────────────────────────────

# Interactive K8s setup (secrets, .env sync)
setup:
    #!/usr/bin/env bash
    set -euo pipefail
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
    info()  { echo -e "${CYAN}  $1${NC}"; }
    ok()    { echo -e "${GREEN}  $1${NC}"; }
    warn()  { echo -e "${YELLOW}  $1${NC}"; }
    err()   { echo -e "${RED}  $1${NC}"; }

    if ! command -v kubectl &>/dev/null; then
        err "kubectl not found. Install: https://kubernetes.io/docs/tasks/tools/"
        exit 1
    fi

    if ! kubectl cluster-info &>/dev/null 2>&1; then
        err "No K8s cluster reachable. Is kubectl configured?"
        exit 1
    fi

    kubectl get namespace {{NAMESPACE}} &>/dev/null 2>&1 || {
        info "Namespace '{{NAMESPACE}}' not found — creating..."
        kubectl create namespace {{NAMESPACE}}
        ok "Namespace created"
    }

    if [ ! -f .env ]; then
        warn ".env not found — copying .env.example"
        cp .env.example .env
    fi

    set -a; source .env; set +a

    env_val() { grep "^${1}=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }
    prompt_val() {
        local key="$1" label="$2" default="$3" is_secret="${4:-false}"
        if [ -n "$default" ]; then
            if $is_secret; then
                printf "  ${CYAN}${label}${NC} [*****]: " >&2
            else
                printf "  ${CYAN}${label}${NC} [${default}]: " >&2
            fi
        else
            printf "  ${CYAN}${label}${NC}: " >&2
        fi
        local user_val
        read -r user_val
        if [ -n "$user_val" ]; then echo "$user_val"; else echo "$default"; fi
    }

    echo -e "${BOLD}  Configuration${NC}"
    echo "  ───────────────────────────────────────────────────────────"

    GITLAB_HOST=$(prompt_val "GITLAB_HOST" "GitLab Host" "$(env_val GITLAB_HOST)")
    GIT_USER=$(prompt_val "GIT_USER" "Git User" "$(env_val GIT_USER)")
    GITLAB_TOKEN=$(prompt_val "GITLAB_TOKEN" "GitLab Token (PAT)" "$(env_val GITLAB_TOKEN)" true)
    OLLAMA_HOST=$(prompt_val "OLLAMA_HOST" "Ollama Host" "$(env_val OLLAMA_HOST)")
    OLLAMA_BASE_URL=$(prompt_val "OLLAMA_BASE_URL" "Ollama Base URL (/v1)" "$(env_val OLLAMA_BASE_URL)")
    OLLAMA_MODEL=$(prompt_val "OLLAMA_MODEL" "Ollama Model" "$(env_val OLLAMA_MODEL)")
    OPENCODE_MODEL=$(prompt_val "OPENCODE_MODEL" "OpenCode Model" "$(env_val OPENCODE_MODEL)")
    OLLAMA_CLOUD_API_KEY=$(prompt_val "OLLAMA_CLOUD_API_KEY" "Ollama Cloud API Key (optional)" "$(env_val OLLAMA_CLOUD_API_KEY)" true)
    AGENT_IMAGE=$(prompt_val "AGENT_IMAGE" "Agent Image" "$(env_val AGENT_IMAGE)")
    BRANCH_FALLBACK_ORDER=$(prompt_val "BRANCH_FALLBACK_ORDER" "Branch Fallback Order" "$(env_val BRANCH_FALLBACK_ORDER)")
    DRY_RUN=$(prompt_val "DRY_RUN" "Dry Run (true/false)" "$(env_val DRY_RUN)")

    MISSING=""
    [ -z "$GITLAB_HOST" ] && MISSING="$MISSING GITLAB_HOST"
    [ -z "$OLLAMA_HOST" ] && MISSING="$MISSING OLLAMA_HOST"
    [ -z "$OLLAMA_MODEL" ] && MISSING="$MISSING OLLAMA_MODEL"
    [ -z "$OPENCODE_MODEL" ] && MISSING="$MISSING OPENCODE_MODEL"
    if [ -n "$MISSING" ]; then
        err "Required values missing:$MISSING"
        exit 1
    fi

    update_env_line() {
        local key="$1" val="$2"
        if grep -q "^${key}=" .env 2>/dev/null; then
            sed -i '' "s|^${key}=.*|${key}=${val}|" .env
        else
            echo "${key}=${val}" >> .env
        fi
    }

    info "Syncing .env..."
    update_env_line "GITLAB_HOST" "$GITLAB_HOST"
    update_env_line "GIT_USER" "$GIT_USER"
    update_env_line "GITLAB_TOKEN" "$GITLAB_TOKEN"
    update_env_line "OLLAMA_HOST" "$OLLAMA_HOST"
    update_env_line "OLLAMA_BASE_URL" "$OLLAMA_BASE_URL"
    update_env_line "OLLAMA_MODEL" "$OLLAMA_MODEL"
    update_env_line "OPENCODE_MODEL" "$OPENCODE_MODEL"
    update_env_line "OLLAMA_CLOUD_API_KEY" "$OLLAMA_CLOUD_API_KEY"
    update_env_line "AGENT_IMAGE" "$AGENT_IMAGE"
    update_env_line "BRANCH_FALLBACK_ORDER" "$BRANCH_FALLBACK_ORDER"
    update_env_line "DRY_RUN" "$DRY_RUN"
    ok ".env updated"

    # Update/create K8s secret
    SECRET_DATA="{\"stringData\":{\"AGENT_IMAGE\":\"{{REGISTRY}}/hivemind-opencode:{{VERSION}}\",\"GIT_HOST\":\"${GITLAB_HOST}\",\"GIT_USER\":\"${GIT_USER}\",\"GIT_TOKEN\":\"${GITLAB_TOKEN}\",\"OLLAMA_HOST\":\"${OLLAMA_HOST}\",\"OLLAMA_BASE_URL\":\"${OLLAMA_BASE_URL}\",\"OLLAMA_MODEL\":\"${OLLAMA_MODEL}\",\"OPENCODE_MODEL\":\"${OPENCODE_MODEL}\",\"BRANCH_FALLBACK_ORDER\":\"${BRANCH_FALLBACK_ORDER}\",\"DRY_RUN\":\"${DRY_RUN}\""
    if [ -n "$OLLAMA_CLOUD_API_KEY" ]; then
        SECRET_DATA="${SECRET_DATA},\"OLLAMA_CLOUD_API_KEY\":\"${OLLAMA_CLOUD_API_KEY}\""
    fi
    SECRET_DATA="${SECRET_DATA}}}"

    if kubectl get secret orchestrator-env -n {{NAMESPACE}} >/dev/null 2>&1; then
        kubectl patch secret orchestrator-env -n {{NAMESPACE}} --type merge -p "$SECRET_DATA"
        ok "Updated secret orchestrator-env"
    else
        kubectl create secret generic orchestrator-env -n {{NAMESPACE}} \
            --from-literal=AGENT_IMAGE="{{REGISTRY}}/hivemind-opencode:{{VERSION}}" \
            --from-literal=GIT_HOST="${GITLAB_HOST}" \
            --from-literal=GIT_USER="${GIT_USER}" \
            --from-literal=GIT_TOKEN="${GITLAB_TOKEN}" \
            --from-literal=OLLAMA_HOST="${OLLAMA_HOST}" \
            --from-literal=OLLAMA_BASE_URL="${OLLAMA_BASE_URL}" \
            --from-literal=OLLAMA_MODEL="${OLLAMA_MODEL}" \
            --from-literal=OPENCODE_MODEL="${OPENCODE_MODEL}" \
            --from-literal=BRANCH_FALLBACK_ORDER="${BRANCH_FALLBACK_ORDER}" \
            --from-literal=DRY_RUN="${DRY_RUN}"
        ok "Created secret orchestrator-env"
    fi

    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "  Setup complete"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo "  Secret:     orchestrator-env (namespace: {{NAMESPACE}})"
    echo "  Next step:  just deploy"

# Build + deploy to K8s (patch bump)
deploy: (_bump-version "patch") docker-build-k8s _deploy-k8s

# Re-deploy current version without rebuild
deploy-skip: _deploy-k8s

# Build + deploy via Docker Compose (local testing)
deploy-docker: docker-build-compose

# ─── Integration Test ─────────────────────────────────────────

# Full integration test (Docker-based)
test-integration:
    #!/usr/bin/env bash
    set -euo pipefail
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
    step()  { echo -e "\n${BLUE}═══ $1 ═══${NC}"; }
    ok()    { echo -e "  ${GREEN}✅ $1${NC}"; }
    warn()  { echo -e "  ${YELLOW}⚠️  $1${NC}"; }

    DRY_RUN="${DRY_RUN:-true}"

    step "1. Docker check"
    docker version --format '{{"{{"}}.Server.Version{{"}}"}}' >/dev/null 2>&1 \
        && ok "Docker running" \
        || { echo -e "  ${RED}❌ Docker not available${NC}"; exit 1; }

    step "2. Build images"
    just docker-build-go
    ok "Orchestrator image built"
    just docker-build-agent
    ok "Agent image built"

    step "3. Start services (Docker Compose)"
    docker compose --profile full-local up --build --remove-orphans -d orchestrator-serve
    ok "Orchestrator started"

    step "4. Health check"
    for i in $(seq 1 30); do
        if curl -sf http://localhost:8080/healthz >/dev/null 2>&1; then
            ok "Orchestrator healthy"
            break
        fi
        echo "  Waiting for orchestrator... ($i/30)"
        sleep 2
    done

    step "5. Submit test ticket"
    if [ -f Orchestrator/example_ticket.json ]; then
        curl -sf -X POST http://localhost:8080/api/tickets \
            -H "Content-Type: application/json" \
            -d @Orchestrator/example_ticket.json && ok "Ticket submitted" || warn "Ticket submission failed"
    else
        warn "No example_ticket.json found"
    fi

    step "6. Metrics"
    curl -sf http://localhost:8080/metrics | head -20 || warn "Metrics not available"

    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Integration test complete${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo "  Orchestrator:  http://localhost:8080"
    echo "  Health:        http://localhost:8080/healthz"
    echo "  Ready:         http://localhost:8080/readyz"
    echo "  Metrics:       http://localhost:8080/metrics"
    echo ""
    echo "  Stop:          docker compose --profile full-local down"

# ─── Internal Recipes (not shown in --list) ──────────────────

_bump-version TYPE:
    #!/usr/bin/env bash
    set -e
    CURRENT="{{VERSION}}"
    IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"
    MAJOR="${MAJOR:-0}"; MINOR="${MINOR:-0}"; PATCH="${PATCH:-0}"
    case "{{TYPE}}" in
        major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
        minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
        patch) PATCH=$((PATCH + 1)) ;;
    esac
    NEW="${MAJOR}.${MINOR}.${PATCH}"
    echo "$NEW" > .version
    echo "Version: ${CURRENT} → ${NEW}"

docker-build-compose:
    docker compose --profile full-local build orchestrator-serve

docker-build-k8s:
    docker build -f Dockerfile.go \
        -t {{REGISTRY}}/hivemind-orchestrator:{{VERSION}} \
        -t {{REGISTRY}}/hivemind-orchestrator:latest \
        .
    docker build \
        -t {{REGISTRY}}/hivemind-opencode:{{VERSION}} \
        -t {{REGISTRY}}/hivemind-opencode:latest \
        -f Agent/Dockerfile Agent/

_deploy-k8s:
    #!/usr/bin/env bash
    set -euo pipefail
    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  Deploying v{{VERSION}} to Kubernetes"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""

    KUSTOMIZATION="deploy/kustomize/base/kustomization.yaml"
    if [ -f "$KUSTOMIZATION" ]; then
        sed -i '' "s/newTag: .*/newTag: {{VERSION}}/" "$KUSTOMIZATION"
        echo "Updated kustomization.yaml → newTag: {{VERSION}}"
    fi

    DEPLOYMENT_YAML="deploy/kustomize/base/deployment.yaml"
    if [ -f "$DEPLOYMENT_YAML" ]; then
        sed -i '' "s|hivemind-orchestrator:[0-9]*\.[0-9]*\.[0-9]*|hivemind-orchestrator:{{VERSION}}|g" "$DEPLOYMENT_YAML"
        echo "Updated deployment.yaml → image: {{VERSION}}"
    fi

    kubectl delete deployment {{DEPLOYMENT}} -n {{NAMESPACE}} --force --grace-period=0 2>/dev/null || true
    kubectl delete pods -n {{NAMESPACE}} -l app=orchestrator --force --grace-period=0 2>/dev/null || true
    sleep 2

    echo ""
    echo "--- Importing images into K8s worker nodes ---"
    WORKER_NODES=$(kubectl get nodes -o name 2>/dev/null | grep -v control-plane || true)
    IMAGE_TAR="/tmp/hivemind-images-v{{VERSION}}.tar"
    if [ -z "$WORKER_NODES" ]; then
        echo "No worker nodes found — images must be available via registry"
    else
        docker save {{REGISTRY}}/hivemind-orchestrator:{{VERSION}} {{REGISTRY}}/hivemind-opencode:{{VERSION}} \
            -o "$IMAGE_TAR"
        for node in $WORKER_NODES; do
            node_name="${node#node/}"
            echo "Importing images into $node_name..."
            cat "$IMAGE_TAR" | docker exec -i "$node_name" sh -c "cat > /tmp/hivemind-images.tar" 2>/dev/null || true
            docker exec "$node_name" ctr -n k8s.io images import /tmp/hivemind-images.tar 2>/dev/null || true
        done
        rm -f "$IMAGE_TAR"
        echo "Images imported into worker nodes"
    fi

    echo ""
    echo "--- Applying Kustomize manifests ---"
    kubectl apply -k deploy/kustomize/base/

    ENABLE_MONITORING="${ENABLE_MONITORING:-false}"
    if [ "$ENABLE_MONITORING" = "true" ] || [ "$ENABLE_MONITORING" = "y" ]; then
        echo "--- Applying monitoring manifests ---"
        kubectl apply -f Orchestrator/k8s/monitoring.yaml
    fi

    echo ""
    echo "--- Waiting for rollout ---"
    kubectl rollout status deployment/{{DEPLOYMENT}} -n {{NAMESPACE}} --timeout=120s

    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  Deployed v{{VERSION}}"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""
    echo "  Version:  v{{VERSION}}"
    echo "  Pod:      $(kubectl get pods -n {{NAMESPACE}} -l app=orchestrator -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo 'pending...')"
    echo "  Status:   $(kubectl get pods -n {{NAMESPACE}} -l app=orchestrator -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo 'pending...')"
    echo "  Service:  $(kubectl get svc -n {{NAMESPACE}} orchestrator -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo 'N/A'):8080"
    echo ""
    echo "  Port-forward:   kubectl port-forward -n {{NAMESPACE}} deployment/{{DEPLOYMENT}} 8080:8080"
    echo "  Web UI:         http://localhost:8080"

# ─── Local K8s (kind) ──────────────────────────────────────────

# Build images for local kind deployment
docker-build-local:
    docker build -f Dockerfile.go -t hivemind-orchestrator:latest .
    docker build -t hivemind-opencode:latest -f Agent/Dockerfile Agent/

# Load images into kind cluster
kind-load: docker-build-local
    kind load docker-image hivemind-orchestrator:latest
    kind load docker-image hivemind-opencode:latest

# Create secrets from .env for local deployment
local-secrets:
    #!/usr/bin/env bash
    set -euo pipefail
    set -a; source .env 2>/dev/null || true; set +a
    kubectl create secret generic orchestrator-env -n hivemind \
        --from-literal=GITLAB_HOST="${GITLAB_HOST:-}" \
        --from-literal=GITLAB_TOKEN="${GITLAB_TOKEN:-}" \
        --from-literal=HIVEMIND_API_KEY="${HIVEMIND_API_KEY:-}" \
        --from-literal=OLLAMA_HOST="${OLLAMA_HOST:-}" \
        --from-literal=OLLAMA_MODEL="${OLLAMA_MODEL:-}" \
        --from-literal=OPENCODE_MODEL="${OPENCODE_MODEL:-}" \
        --from-literal=VCS_PROVIDER="${VCS_PROVIDER:-gitlab}" \
        --dry-run=client -o yaml | kubectl apply -f -
    kubectl create secret generic orchestrator-db -n hivemind \
        --from-literal=url="postgres://hivemind:hivemind@hivemind-postgres:5432/hivemind?sslmode=disable" \
        --dry-run=client -o yaml | kubectl apply -f -
    kubectl create secret generic orchestrator-redis -n hivemind \
        --from-literal=url="redis://redis:6379" \
        --dry-run=client -o yaml | kubectl apply -f -

# Deploy to local kind cluster
deploy-local: kind-load local-secrets
    #!/usr/bin/env bash
    set -euo pipefail
    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  Deploying HiveMind to local kind cluster"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""
    echo "--- Applying Kustomize manifests ---"
    kubectl apply -k deploy/kustomize/overlays/local/
    echo ""
    echo "--- Waiting for Orchestrator ---"
    kubectl rollout status deployment/orchestrator -n hivemind --timeout=120s
    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  HiveMind deployed to local kind cluster!"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""
    echo "  Access services:"
    echo "    kubectl port-forward -n hivemind svc/orchestrator 8080:8080"
    echo "    kubectl port-forward -n hivemind svc/grafana 3000:3000"
    echo "    kubectl port-forward -n hivemind svc/prometheus 9090:9090"
    echo ""

# Tear down local deployment
local-teardown:
    kubectl delete namespace hivemind

# Quick access - port-forward all services
local-access:
    #!/usr/bin/env bash
    echo "Port-forwarding HiveMind services (Ctrl+C to stop)..."
    echo "  Orchestrator: http://localhost:8080"
    echo "  Grafana:      http://localhost:3000 (admin/hivemind)"
    echo "  Prometheus:   http://localhost:9090"
    kubectl port-forward -n hivemind svc/orchestrator 8080:8080 &
    kubectl port-forward -n hivemind svc/grafana 3000:3000 &
    kubectl port-forward -n hivemind svc/prometheus 9090:9090 &
    wait