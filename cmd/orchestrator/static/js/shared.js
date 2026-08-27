const HM = {
  escapeHtml(str) {
    if (str == null) return '';
    const d = document.createElement('div');
    d.textContent = String(str);
    return d.innerHTML;
  },

  apiKey: null,

  getApiKey() {
    if (this.apiKey) return this.apiKey;
    this.apiKey = localStorage.getItem('hivemind_api_key') || '';
    return this.apiKey;
  },

  setApiKey(key) {
    this.apiKey = key;
    localStorage.setItem('hivemind_api_key', key);
  },

  api(path, method = 'GET', body = null) {
    const opts = { method };
    const headers = {};
    const key = this.getApiKey();
    if (key) headers['Authorization'] = 'Bearer ' + key;
    if (body) {
      headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    if (Object.keys(headers).length) opts.headers = headers;
    return fetch('/api' + path, opts).then(res => {
      if (res.status === 401 && !key) {
        const newKey = prompt('HiveMind API Key required.\nEnter your API key:');
        if (newKey) {
          this.setApiKey(newKey.trim());
          return this.api(path, method, body);
        }
      }
      if (res.status === 401 && key) {
        localStorage.removeItem('hivemind_api_key');
        this.apiKey = null;
        const newKey = prompt('API key invalid. Enter correct API key:');
        if (newKey) {
          this.setApiKey(newKey.trim());
          return this.api(path, method, body);
        }
      }
      if (!res.ok) {
        return res.json().catch(() => ({ error: res.statusText })).then(err => {
          throw new Error(err.error || err.detail || res.statusText);
        });
      }
      return res.json().then(data => data === null ? [] : data);
    });
  },

  toast(msg, type = 'success') {
    const t = document.getElementById('toast');
    if (!t) return;
    t.textContent = msg;
    t.className = 'toast ' + type;
    t.classList.add('show');
    clearTimeout(t._timer);
    t._timer = setTimeout(() => t.classList.remove('show'), 3000);
  },

  debounce(fn, ms = 300) {
    let timer;
    return function (...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), ms);
    };
  },

  sse: {
    source: null,
    listeners: [],
    _reconnectTimer: null,

    connect(handlers = {}) {
      this.disconnect();
      this.source = new EventSource('/api/stream');
      this.source.onopen = () => {
        const dot = document.getElementById('connStatus');
        const lbl = document.getElementById('connLabel');
        if (dot) dot.classList.remove('offline');
        if (lbl) lbl.textContent = 'Connected';
      };
      this.source.onerror = () => {
        const dot = document.getElementById('connStatus');
        const lbl = document.getElementById('connLabel');
        if (dot) dot.classList.add('offline');
        if (lbl) lbl.textContent = 'Connection lost';
        this.source.close();
        this._reconnectTimer = setTimeout(() => this.connect(handlers), 5000);
      };
      const eventTypes = ['message', 'queue_updated', 'agent_updated', 'ticket_created', 'ticket_completed', 'ticket_merged', 'ticket_requeued', 'repos_updated'];
      eventTypes.forEach(type => {
        this.source.addEventListener(type, () => {
          if (handlers.onEvent) handlers.onEvent(type);
        });
      });
    },

    disconnect() {
      clearTimeout(this._reconnectTimer);
      if (this.source) {
        this.source.close();
        this.source = null;
      }
    }
  },

  async loadVersion() {
    try {
      const cfg = await this.api('/config');
      const el = document.getElementById('versionLabel');
      if (el && cfg.version) el.textContent = 'Orchestrator v' + cfg.version;
    } catch (e) {}
  },

  timeAgo(dateStr) {
    if (!dateStr) return '';
    const now = new Date();
    const d = new Date(dateStr);
    const diff = Math.floor((now - d) / 1000);
    if (diff < 60) return 'just now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
  },

  priorityClass(p) {
    return { Critical: 'critical', High: 'high', Medium: 'medium', Low: 'low' }[p] || 'medium';
  },

  pipelineBadge(status) {
    if (!status || status === 'unknown') return '';
    const cls = (status === 'success' || status === 'passed') ? 'success' :
                status === 'failed' ? 'failed' :
                (status === 'running' || status === 'pending') ? 'running' : 'unknown';
    return '<span class="badge badge-pipeline-' + cls + '">CI: ' + HM.escapeHtml(status) + '</span>';
  }
};