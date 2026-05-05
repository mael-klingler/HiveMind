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
FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    ca-certificates \
    curl \
    jq \
    gettext-base \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @gitbeaker/rest
RUN npm install -g opencode-ai@latest
ENV NODE_PATH=/usr/local/lib/node_modules

# Copy LeanKG binary from builder
COPY --from=leankg-builder /usr/local/bin/leankg /usr/local/bin/leankg
RUN leankg --version

# Install snip CLI (required by opencode-snip plugin)
RUN curl -fsSL https://raw.githubusercontent.com/edouard-claude/snip/master/install.sh | sh \
    && snip --version 2>/dev/null || echo "snip installed"

RUN mkdir -p /root/.ssh /scripts /workspace /etc/agent
RUN ssh-keyscan gitlab.com >> /root/.ssh/known_hosts 2>/dev/null || true
RUN printf "Host *.gitlab.com\n  StrictHostKeyChecking accept-new\n  UserKnownHostsFile /root/.ssh/known_hosts\n" >> /root/.ssh/config

RUN echo '#!/usr/bin/env node\n'\
'const { Gitlab } = require("@gitbeaker/rest");\n'\
'const api = new Gitlab({ token: process.env.GITLAB_TOKEN, host: process.env.GITLAB_HOST || "https://gitlab.com" });\n'\
'const cmd = process.argv[2];\n'\
'const run = async () => {\n'\
'  if (cmd === "mr") {\n'\
'    const args = {};\n'\
'    for (let i = 3; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, "")] = process.argv[i + 1];\n'\
'    const mr = await api.MergeRequests.create(args.projectId || args.id, args.source_branch, args.target_branch, args.title, { description: args.description || "", remove_source_branch: true });\n'\
'    console.log(mr.web_url);\n'\
'  } else if (cmd === "list") {\n'\
'    const args = {};\n'\
'    for (let i = 3; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, "")] = process.argv[i + 1];\n'\
'    const mrs = await api.MergeRequests.all({ projectId: args.id, sourceBranch: args.source_branch, targetBranch: args.target_branch, state: "opened" });\n'\
'    if (mrs.length > 0) console.log(JSON.stringify(mrs.map(m => ({ title: m.title }))));\n'\
'  }\n'\
'};\n'\
'run().catch(e => { console.error(e.message); process.exit(1); });'\
 > /usr/local/bin/glab.js && chmod +x /usr/local/bin/glab.js

COPY opencode.json.template /etc/agent/opencode.json.template
COPY entrypoint.sh /scripts/entrypoint.sh
RUN chmod +x /scripts/entrypoint.sh

EXPOSE 4096

WORKDIR /workspace
ENTRYPOINT ["/scripts/entrypoint.sh"]
