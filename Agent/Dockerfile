FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    ca-certificates \
    curl \
    jq \
    gettext-base \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /home/hivemind/.ssh /scripts /workspace /etc/agent && \
    usermod -d /home/hivemind -l hivemind node 2>/dev/null || true && \
    chown -R 1000:1000 /home/hivemind /scripts /workspace /etc/agent

RUN npm install -g @gitbeaker/rest@2.5.0
RUN npm install -g opencode-ai@0.20.0
ENV NODE_PATH=/usr/local/lib/node_modules

RUN curl -fsSL https://github.com/edouard-claude/snip/releases/download/v0.3.2/snip-linux-x64.tar.gz \
    | tar xz -C /usr/local/bin/ \
    && snip --version 2>/dev/null || echo "snip installed"

RUN ssh-keyscan gitlab.com >> /home/hivemind/.ssh/known_hosts 2>/dev/null || true
RUN printf "Host *.gitlab.com\n  StrictHostKeyChecking accept-new\n  UserKnownHostsFile /home/hivemind/.ssh/known_hosts\n" >> /home/hivemind/.ssh/config

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

USER hivemind
EXPOSE 4096

WORKDIR /workspace
ENTRYPOINT ["/scripts/entrypoint.sh"]
