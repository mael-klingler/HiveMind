#!/bin/bash
set -euo pipefail

# =============================================================================
# HiveMind – Versioned Build & Deploy
# =============================================================================
# Usage:
#   ./redeploy.sh                     # Build + Deploy (patch bump)
#   ./redeploy.sh --minor             # Build + Deploy (minor bump)
#   ./redeploy.sh --major             # Build + Deploy (major bump)
#   ./redeploy.sh --version 1.5.0     # Build + Deploy (explicit version)
#   ./redeploy.sh --skip-build        # Only re-deploy current version to K8s
#   ./redeploy.sh --docker            # Rebuild docker-compose images
#   ./redeploy.sh --tag               # Create git tag for current version
#   ./redeploy.sh --changelog         # Generate changelog since last tag
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NAMESPACE="hivemind"
DEPLOYMENT_NAME="orchestrator"

SKIP_BUILD=false
DOCKER_MODE=false
NO_CACHE=false
TAG_ONLY=false
CHANGELOG_ONLY=false
VERSION_EXPLICIT=""
BUMP_TYPE="patch"

# ── Version File ─────────────────────────────────────────────────────────────
VERSION_FILE="$SCRIPT_DIR/.version"

get_current_version() {
  if [[ -f "$VERSION_FILE" ]]; then
    cat "$VERSION_FILE"
  else
    echo "0.1.0"
  fi
}

set_version() {
  echo "$1" > "$VERSION_FILE"
}

bump_version() {
  local current="$1"
  local type="$2"
  local major minor patch

  IFS='.' read -r major minor patch <<< "$current"
  major="${major:-0}"
  minor="${minor:-0}"
  patch="${patch:-0}"

  case "$type" in
    major) major=$((major + 1)); minor=0; patch=0 ;;
    minor) minor=$((minor + 1)); patch=0 ;;
    patch) patch=$((patch + 1)) ;;
  esac

  echo "${major}.${minor}.${patch}"
}

# ── Parse args ──────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build)  SKIP_BUILD=true; shift ;;
    --docker)      DOCKER_MODE=true; shift ;;
    --no-cache)    NO_CACHE=true; shift ;;
    --tag)         TAG_ONLY=true; shift ;;
    --changelog)   CHANGELOG_ONLY=true; shift ;;
    --major)       BUMP_TYPE="major"; shift ;;
    --minor)       BUMP_TYPE="minor"; shift ;;
    --patch)       BUMP_TYPE="patch"; shift ;;
    --version)
      if [[ -z "${2:-}" ]]; then echo "❌ --version requires an argument"; exit 1; fi
      VERSION_EXPLICIT="$2"; shift 2 ;;
    --help|-h)
      echo "HiveMind – Versioned Build & Deploy"
      echo ""
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --no-cache      Remove all related images before building (clean build)"
      echo "  --skip-build    Skip Docker image build, only redeploy to K8s"
      echo "  --docker        Rebuild docker-compose images (local Docker mode)"
      echo "  --major         Bump major version (x.0.0)"
      echo "  --minor         Bump minor version (0.x.0)"
      echo "  --patch         Bump patch version (0.0.x) [default]"
      echo "  --version VER   Use explicit version (e.g. --version 2.0.0)"
      echo "  --tag           Create git tag for current version (no deploy)"
      echo "  --changelog     Generate changelog since last tag"
      echo "  --help          Show this help"
      echo ""
      echo "Examples:"
      echo "  $0                    # patch bump + build + deploy"
      echo "  $0 --minor            # minor bump + build + deploy"
      echo "  $0 --version 1.0.0   # set version to 1.0.0 + build + deploy"
      echo "  $0 --skip-build       # redeploy current version without rebuild"
      echo "  $0 --tag              # create git tag for current version"
      echo "  $0 --changelog        # show changelog since last tag"
      exit 0
      ;;
    *) echo "❌ Unknown argument: $1"; exit 1 ;;
  esac
done

# ── Resolve version ─────────────────────────────────────────────────────────
CURRENT_VERSION="$(get_current_version)"

if [[ -n "$VERSION_EXPLICIT" ]]; then
  NEW_VERSION="$VERSION_EXPLICIT"
else
  NEW_VERSION="$(bump_version "$CURRENT_VERSION" "$BUMP_TYPE")"
fi

# ── Changelog ───────────────────────────────────────────────────────────────
if $CHANGELOG_ONLY; then
  PREV_TAG="$(git tag --sort=-v:refname | head -1)" || true
  echo ""
  echo "═══════════════════════════════════════════════════════════════"
  echo "  Changelog ${PREV_TAG:-v0.0.0} → v${CURRENT_VERSION}"
  echo "═══════════════════════════════════════════════════════════════"
  echo ""
  if [[ -n "$PREV_TAG" ]]; then
    git log "${PREV_TAG}..HEAD" --pretty=format:"  %h  %s" --no-merges
  else
    git log --pretty=format:"  %h  %s" --no-merges -20
  fi
  echo ""
  echo ""
  exit 0
fi

# ── Tag-only mode ───────────────────────────────────────────────────────────
if $TAG_ONLY; then
  echo ""
  echo "🏷️  Creating git tag: v${CURRENT_VERSION}"
  git tag -a "v${CURRENT_VERSION}" -m "Release v${CURRENT_VERSION}"
  echo "✅ Tag v${CURRENT_VERSION} created"
  echo ""
  echo "Push with: git push origin v${CURRENT_VERSION}"
  exit 0
fi

set_version "$NEW_VERSION"

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  🚀 HiveMind v${NEW_VERSION}"
echo "═══════════════════════════════════════════════════════════════"
echo "  Previous: v${CURRENT_VERSION}"
echo "  New:      v${NEW_VERSION}"
echo ""

# ═══════════════════════════════════════════════════════════════════════════════
# 1. NO-CACHE: Remove old images
# ═══════════════════════════════════════════════════════════════════════════════
if $NO_CACHE && ! $SKIP_BUILD; then
  echo ""
  echo "═══════════════════════════════════════════════════════════════"
  echo "  🗑️  Removing old HiveMind images (--no-cache)"
  echo "═══════════════════════════════════════════════════════════════"
  echo ""

  REMOVED=0
  for IMG in "hivemind-opencode" "hivemind-orchestrator"; do
    TAGS=$(docker image ls "$IMG" --format '{{.Tag}}' 2>/dev/null || true)
    if [[ -n "$TAGS" ]]; then
      while IFS= read -r tag; do
        [[ -z "$tag" ]] && continue
        echo "   🗑  $IMG:$tag"
        docker rmi "$IMG:$tag" 2>/dev/null || true
        REMOVED=$((REMOVED + 1))
      done <<< "$TAGS"
    else
      echo "   –  $IMG: no images found"
    fi
  done

  # Also prune dangling build cache
  docker builder prune -f --filter "until=1h" >/dev/null 2>&1 || true

  echo ""
  echo "✅ Removed ${REMOVED} image(s)"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# 2. BUILD IMAGES
# ═══════════════════════════════════════════════════════════════════════════════
if ! $SKIP_BUILD; then
  echo ""
  echo "═══════════════════════════════════════════════════════════════"
  echo "  Building Docker Images (v${NEW_VERSION})"
  echo "═══════════════════════════════════════════════════════════════"
  echo ""

  if $DOCKER_MODE; then
    echo "Building docker-compose images..."
    cd "$SCRIPT_DIR"
    if $NO_CACHE; then
      docker compose --profile full build --no-cache
    else
      docker compose --profile full build
    fi
  else
    if $NO_CACHE; then
      NO_CACHE_FLAG="--no-cache"
    else
      NO_CACHE_FLAG=""
    fi

    echo "📦 Building Agent Image..."
    docker build \
      ${NO_CACHE_FLAG} \
      -t "hivemind-opencode:${NEW_VERSION}" \
      -t "hivemind-opencode:latest" \
      -f "$SCRIPT_DIR/Agent/Dockerfile" \
      "$SCRIPT_DIR/Agent"

    echo ""
    echo "📦 Building Orchestrator Image..."
    docker build \
      ${NO_CACHE_FLAG} \
      -t "hivemind-orchestrator:${NEW_VERSION}" \
      -t "hivemind-orchestrator:latest" \
      -f "$SCRIPT_DIR/Orchestrator/Dockerfile" \
      "$SCRIPT_DIR/Orchestrator"
  fi

  echo ""
  echo "Images built:"
  echo "   hivemind-opencode:${NEW_VERSION}"
  echo "   hivemind-orchestrator:${NEW_VERSION}"
else
  echo "Skipping build (--skip-build), using v${CURRENT_VERSION}"
  NEW_VERSION="$CURRENT_VERSION"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# 2. DEPLOY TO KUBERNETES
# ═══════════════════════════════════════════════════════════════════════════════
if ! $DOCKER_MODE; then
  echo ""
  echo "═══════════════════════════════════════════════════════════════"
  echo "  ☸️  Deploying v${NEW_VERSION} to Kubernetes"
  echo "═══════════════════════════════════════════════════════════════"
  echo ""

  # Update kustomization image tag
  KUSTOMIZATION="$SCRIPT_DIR/Orchestrator/kustomize/base/kustomization.yaml"
  if [[ -f "$KUSTOMIZATION" ]]; then
    sed -i '' "s/newTag: .*/newTag: ${NEW_VERSION}/" "$KUSTOMIZATION"
    echo "📝 Updated kustomization.yaml → newTag: ${NEW_VERSION}"
  fi

  # Update deployment image tags (both images: orchestrator + agent)
  DEPLOYMENT_YAML="$SCRIPT_DIR/Orchestrator/kustomize/base/deployment.yaml"
  if [[ -f "$DEPLOYMENT_YAML" ]]; then
    sed -i '' "s|hivemind-orchestrator:[0-9]*\.[0-9]*\.[0-9]*|hivemind-orchestrator:${NEW_VERSION}|g" "$DEPLOYMENT_YAML"
    echo "📝 Updated deployment.yaml → image: ${NEW_VERSION}"
  fi

   # Read .env values
   if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
     echo "❌ .env Datei nicht gefunden unter $SCRIPT_DIR/.env"
     echo "   Kopiere .env.example und passe an:  cp .env.example .env"
     echo "   Oder fuehre ./setup.sh aus"
     exit 1
   fi
   OLLAMA_CLOUD_API_KEY=$(grep "^OLLAMA_CLOUD_API_KEY=" "$SCRIPT_DIR/.env" | cut -d= -f2- || true)
   GIT_HOST=$(grep "^GITLAB_HOST=" "$SCRIPT_DIR/.env" | cut -d= -f2- || true)
   GIT_TOKEN=$(grep "^GITLAB_TOKEN=" "$SCRIPT_DIR/.env" | cut -d= -f2- || true)
   GIT_USER=$(grep "^GIT_USER=" "$SCRIPT_DIR/.env" | cut -d= -f2- || echo "gitlab-ci-token")
   OLLAMA_HOST=$(grep "^OLLAMA_HOST=" "$SCRIPT_DIR/.env" | cut -d= -f2-)
   OLLAMA_BASE_URL=$(grep "^OLLAMA_BASE_URL=" "$SCRIPT_DIR/.env" | cut -d= -f2-)
    OLLAMA_MODEL=$(grep "^OLLAMA_MODEL=" "$SCRIPT_DIR/.env" | cut -d= -f2-)
    OPENCODE_MODEL=$(grep "^OPENCODE_MODEL=" "$SCRIPT_DIR/.env" | cut -d= -f2-)
    BRANCH_FALLBACK_ORDER=$(grep "^BRANCH_FALLBACK_ORDER=" "$SCRIPT_DIR/.env" | cut -d= -f2- || echo "development,qa,main")
    DRY_RUN=$(grep "^DRY_RUN=" "$SCRIPT_DIR/.env" | cut -d= -f2- || echo "false")

   if [[ -z "$OLLAMA_HOST" || -z "$OLLAMA_MODEL" || -z "$OPENCODE_MODEL" ]]; then
     echo "❌ Pflicht-Variablen fehlen in .env: OLLAMA_HOST, OLLAMA_MODEL, OPENCODE_MODEL"
     exit 1
   fi

   # Update or create orchestrator-env secret
   echo ""
   echo "--- Updating orchestrator-env secret ---"
    if kubectl get secret "orchestrator-env" -n "$NAMESPACE" >/dev/null 2>&1; then
      SECRET_DATA="{\"stringData\":{\"AGENT_IMAGE\":\"hivemind-opencode:${NEW_VERSION}\",\"GIT_HOST\":\"${GIT_HOST}\",\"GIT_USER\":\"${GIT_USER}\",\"GIT_TOKEN\":\"${GIT_TOKEN}\",\"OLLAMA_HOST\":\"${OLLAMA_HOST}\",\"OLLAMA_BASE_URL\":\"${OLLAMA_BASE_URL}\",\"OLLAMA_MODEL\":\"${OLLAMA_MODEL}\",\"OPENCODE_MODEL\":\"${OPENCODE_MODEL}\",\"BRANCH_FALLBACK_ORDER\":\"${BRANCH_FALLBACK_ORDER}\",\"DRY_RUN\":\"${DRY_RUN}\""
      if [[ -n "$OLLAMA_CLOUD_API_KEY" ]]; then
        SECRET_DATA="${SECRET_DATA},\"OLLAMA_CLOUD_API_KEY\":\"${OLLAMA_CLOUD_API_KEY}\""
      fi
      SECRET_DATA="${SECRET_DATA}}}"
      kubectl patch secret orchestrator-env -n "$NAMESPACE" --type merge -p "$SECRET_DATA"
      echo "📝 Updated orchestrator-env secret → AGENT_IMAGE: ${NEW_VERSION}"
   else
     echo "⚠️  Secret nicht gefunden – erstelle neu..."
     SECRET_ARGS=(
       --from-literal=AGENT_IMAGE="hivemind-opencode:${NEW_VERSION}"
       --from-literal=GIT_HOST="${GIT_HOST}"
       --from-literal=GIT_USER="${GIT_USER}"
       --from-literal=GIT_TOKEN="${GIT_TOKEN}"
       --from-literal=OLLAMA_HOST="${OLLAMA_HOST}"
       --from-literal=OLLAMA_BASE_URL="${OLLAMA_BASE_URL}"
       --from-literal=OLLAMA_MODEL="${OLLAMA_MODEL}"
        --from-literal=OPENCODE_MODEL="${OPENCODE_MODEL}"
        --from-literal=BRANCH_FALLBACK_ORDER="${BRANCH_FALLBACK_ORDER}"
        --from-literal=DRY_RUN="${DRY_RUN}"
     )
     [[ -n "$OLLAMA_CLOUD_API_KEY" ]] && SECRET_ARGS+=(--from-literal=OLLAMA_CLOUD_API_KEY="${OLLAMA_CLOUD_API_KEY}")
     kubectl create secret generic orchestrator-env -n "$NAMESPACE" "${SECRET_ARGS[@]}"
     echo "📝 Created orchestrator-env secret → AGENT_IMAGE: ${NEW_VERSION}"
   fi
  kubectl delete deployment "$DEPLOYMENT_NAME" -n "$NAMESPACE" --force --grace-period=0 2>/dev/null || true
  kubectl delete pods -n "$NAMESPACE" -l app=orchestrator --force --grace-period=0 2>/dev/null || true
  sleep 2

  # ── Import images into K8s worker nodes ────────────────────────────────────
  echo ""
  echo "--- Importing images into K8s worker nodes ---"
  WORKER_NODES=$(kubectl get nodes -o name 2>/dev/null | grep -v control-plane || true)
  IMAGE_TAR="/tmp/hivemind-images-v${NEW_VERSION}.tar"
  if [[ -z "$WORKER_NODES" ]]; then
    echo "⚠️  No worker nodes found – images must be available via registry"
  else
    docker save "hivemind-opencode:${NEW_VERSION}" "hivemind-opencode:latest" \
      "hivemind-orchestrator:${NEW_VERSION}" "hivemind-orchestrator:latest" \
      -o "$IMAGE_TAR"
    for node in $WORKER_NODES; do
      node_name="${node#node/}"
      echo "📦 Importing images into $node_name..."
      cat "$IMAGE_TAR" | docker exec -i "$node_name" sh -c "cat > /tmp/hivemind-images.tar" 2>/dev/null
      docker exec "$node_name" ctr -n k8s.io images import /tmp/hivemind-images.tar 2>/dev/null \
        || echo "⚠️  ctr import failed for $node_name – trying crictl..."
      docker exec "$node_name" crictl pull "docker.io/library/hivemind-opencode:${NEW_VERSION}" 2>/dev/null || true
    done
    rm -f "$IMAGE_TAR"
    echo "✅ Images imported into worker nodes"
  fi

  echo ""
  echo "--- Applying Kustomize manifests ---"
  kubectl apply -k "$SCRIPT_DIR/Orchestrator/kustomize/base/"

  # ── Monitoring (if enabled) ────────────────────────────────────
  ENABLE_MONITORING=$(grep "^ENABLE_MONITORING=" "$SCRIPT_DIR/.env" 2>/dev/null | cut -d= -f2- || echo "false")
  if [[ "$ENABLE_MONITORING" == "true" || "$ENABLE_MONITORING" == "y" ]]; then
    echo ""
    echo "--- Applying monitoring manifests ---"
    kubectl apply -f "$SCRIPT_DIR/Orchestrator/k8s/monitoring.yaml"
  fi

  echo ""
  echo "--- Waiting for rollout ---"
  kubectl rollout status deployment/"$DEPLOYMENT_NAME" -n "$NAMESPACE" --timeout=120s

  echo ""
  echo "═══════════════════════════════════════════════════════════════"
  echo "  ✅ Deployed v${NEW_VERSION}"
  echo "═══════════════════════════════════════════════════════════════"
  echo ""
  echo "   Version:  v${NEW_VERSION}"
  echo "   Pod:      $(kubectl get pods -n "$NAMESPACE" -l app=orchestrator -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo 'pending...')"
  echo "   Status:   $(kubectl get pods -n "$NAMESPACE" -l app=orchestrator -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo 'pending...')"
  echo "   Service:  $(kubectl get svc -n "$NAMESPACE" orchestrator -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo 'N/A'):8080"
  echo ""
   echo "   Port-forward:   kubectl port-forward -n $NAMESPACE deployment/$DEPLOYMENT_NAME 8080:8080"
   echo "   Web UI:         http://localhost:8080"
   if [[ "$ENABLE_MONITORING" == "true" || "$ENABLE_MONITORING" == "y" ]]; then
   echo ""
   echo "   Monitoring:"
   echo "     Grafana:      kubectl port-forward -n $NAMESPACE svc/grafana 3000:3000"
   echo "     Prometheus:   kubectl port-forward -n $NAMESPACE svc/prometheus 9090:9090"
   echo "     Dashboard:    http://localhost:3000  (admin/hivemind)"
   fi
  echo ""

  # ── Git commit + tag ──────────────────────────────────────────────────────
  echo "--- Git ---"
  cd "$SCRIPT_DIR"
  if git diff --quiet && git diff --cached --quiet; then
    echo "No uncommitted changes"
  else
    git add -A
    git commit -m "release: v${NEW_VERSION}" || true
  fi
  git tag -a "v${NEW_VERSION}" -m "Release v${NEW_VERSION}"
  echo "🏷️  Tagged: v${NEW_VERSION}"
  echo ""
  echo "Push with:  git push && git push origin v${NEW_VERSION}"

# ═══════════════════════════════════════════════════════════════════════════════
# 3. DOCKER-COMPOSE MODE
# ═══════════════════════════════════════════════════════════════════════════════
else
  echo ""
  echo "═══════════════════════════════════════════════════════════════"
  echo "  🐳 Starting Docker Compose (v${NEW_VERSION})"
  echo "═══════════════════════════════════════════════════════════════"
  echo ""

  cd "$SCRIPT_DIR"
  docker compose --profile init up --build --remove-orphans

  echo ""
  echo "✅ Docker Compose started (v${NEW_VERSION})"
  echo "   Orchestrator: http://localhost:8080"
fi

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  🎉 Done – v${NEW_VERSION}"
echo "═══════════════════════════════════════════════════════════════"