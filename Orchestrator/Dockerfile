# ── Stage 1: Build LeanKG from source ──────────────────────────────
FROM rust:1-bookworm AS leankg-builder

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/FreePeak/LeanKG.git /leankg-src \
    && cd /leankg-src \
    && cargo build --release \
    && cp target/release/leankg /usr/local/bin/ \
    && /usr/local/bin/leankg --version

# ── Stage 2: Runtime ─────────────────────────────────────────────────
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    curl \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN K8S_ARCH=$(uname -m | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/') \
    && curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/${K8S_ARCH}/kubectl" \
    && install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl \
    && rm kubectl

# Copy LeanKG binary from builder
COPY --from=leankg-builder /usr/local/bin/leankg /usr/local/bin/leankg
RUN leankg --version

RUN groupadd -g 1000 hivemind 2>/dev/null || true && \
    useradd -u 1000 -g 1000 -m -s /bin/bash hivemind 2>/dev/null || true && \
    mkdir -p /home/hivemind/.ssh

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY database.py .
COPY database_pg.py .
COPY server.py .
COPY k8s_client.py .
COPY gitlab_client.py .
COPY pod_builder.py .
COPY vcs/ ./vcs/
COPY static/ ./static/

RUN chown -R 1000:1000 /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
EXPOSE 8080

USER hivemind
ENTRYPOINT ["python3", "main.py"]
CMD ["serve"]

# Override for debug - allow command override
# Kubernetes will use args: ["serve"] to override CMD
