"""Task Intake Server — simple HTTP API for submitting tasks from your phone.

Endpoints:
    POST /task          — Create a new task
    GET  /tasks         — List recent tasks (JSON)
    GET  /tasks/:id     — Get task detail (JSON)
    GET  /stats         — Today's stats (JSON)
    GET  /              — Mobile UI with form + task log

Auth: Bearer token (INTAKE_SECRET from .env)

Usage:
    python -m orchestrator.intake_server
"""

import json
import logging
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

from orchestrator import db
from orchestrator.settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("intake")

# ─── HTML UI (served at GET /) ───

MOBILE_UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>TrendyReports Agent</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { 
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0f172a; color: #e2e8f0; min-height: 100vh;
    padding-bottom: 80px;
  }

  /* ── Header ── */
  .header {
    background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
    padding: 20px 20px 12px;
    border-bottom: 1px solid #1e293b;
  }
  .header h1 { font-size: 22px; color: #38bdf8; }
  .header .sub { font-size: 13px; color: #64748b; margin-top: 2px; }

  /* ── Tabs ── */
  .tabs {
    display: flex; background: #1e293b; border-bottom: 1px solid #334155;
    position: sticky; top: 0; z-index: 10;
  }
  .tab {
    flex: 1; padding: 14px 8px; text-align: center; font-size: 14px; font-weight: 600;
    color: #64748b; cursor: pointer; border-bottom: 2px solid transparent;
    transition: all 0.2s;
  }
  .tab.active { color: #38bdf8; border-bottom-color: #38bdf8; }
  .tab .badge {
    display: inline-block; background: #ef4444; color: white; font-size: 11px;
    padding: 1px 6px; border-radius: 10px; margin-left: 4px; font-weight: 700;
  }

  /* ── Content ── */
  .content { padding: 16px; max-width: 600px; margin: 0 auto; }
  .tab-panel { display: none; }
  .tab-panel.active { display: block; }

  /* ── Form styles ── */
  label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 6px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; }
  input, textarea, select {
    width: 100%; padding: 12px; border: 1px solid #334155; border-radius: 10px;
    background: #1e293b; color: #e2e8f0; font-size: 16px; margin-bottom: 14px;
    -webkit-appearance: none;
  }
  input:focus, textarea:focus, select:focus {
    outline: none; border-color: #38bdf8; box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.12);
  }
  textarea { min-height: 100px; resize: vertical; }
  .row { display: flex; gap: 10px; }
  .row > div { flex: 1; }
  button.primary {
    width: 100%; padding: 14px; border: none; border-radius: 10px;
    background: linear-gradient(135deg, #38bdf8, #0ea5e9); color: #0f172a;
    font-size: 16px; font-weight: 700; cursor: pointer; margin-top: 4px;
    transition: opacity 0.2s;
  }
  button.primary:active { opacity: 0.8; }
  button.primary:disabled { opacity: 0.4; cursor: not-allowed; }

  /* ── Toast ── */
  .toast {
    position: fixed; top: 20px; left: 50%; transform: translateX(-50%);
    padding: 12px 20px; border-radius: 10px; font-size: 14px; font-weight: 600;
    z-index: 100; opacity: 0; transition: opacity 0.3s; pointer-events: none;
    max-width: 90%;
  }
  .toast.success { background: #065f46; border: 1px solid #10b981; color: #34d399; }
  .toast.error { background: #7f1d1d; border: 1px solid #ef4444; color: #fca5a5; }
  .toast.show { opacity: 1; }

  /* ── Stats bar ── */
  .stats-bar {
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px;
    margin-bottom: 16px;
  }
  .stat-card {
    background: #1e293b; border-radius: 10px; padding: 12px 8px;
    text-align: center; border: 1px solid #334155;
  }
  .stat-card .val { font-size: 22px; font-weight: 700; color: #38bdf8; }
  .stat-card .lbl { font-size: 11px; color: #64748b; margin-top: 2px; }
  .stat-card.queued .val { color: #fbbf24; }
  .stat-card.progress .val { color: #a78bfa; }
  .stat-card.done .val { color: #34d399; }
  .stat-card.failed .val { color: #f87171; }

  /* ── Task List ── */
  .task-list { display: flex; flex-direction: column; gap: 8px; }
  .task-card {
    background: #1e293b; border: 1px solid #334155; border-radius: 10px;
    padding: 14px; cursor: pointer; transition: border-color 0.2s;
  }
  .task-card:active { border-color: #38bdf8; }
  .task-card .top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
  .task-card .title { font-size: 15px; font-weight: 600; color: #e2e8f0; line-height: 1.3; }
  .task-card .meta { font-size: 12px; color: #64748b; margin-top: 6px; }

  /* Status pills */
  .pill {
    display: inline-block; font-size: 11px; font-weight: 700; padding: 3px 8px;
    border-radius: 6px; text-transform: uppercase; letter-spacing: 0.5px;
  }
  .pill.queued { background: #422006; color: #fbbf24; }
  .pill.planning, .pill.coding, .pill.reviewing, .pill.testing { background: #1e1b4b; color: #a78bfa; }
  .pill.deploying { background: #042f2e; color: #2dd4bf; }
  .pill.done { background: #052e16; color: #4ade80; }
  .pill.failed { background: #450a0a; color: #f87171; }
  .pill.pr_open { background: #172554; color: #60a5fa; }

  /* Priority dots */
  .priority-dot {
    display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px;
  }
  .priority-dot.urgent { background: #ef4444; }
  .priority-dot.high { background: #f97316; }
  .priority-dot.medium { background: #3b82f6; }
  .priority-dot.low { background: #6b7280; }

  /* ── Task Detail Modal ── */
  .modal-overlay {
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7);
    z-index: 50; align-items: flex-end; justify-content: center;
  }
  .modal-overlay.open { display: flex; }
  .modal {
    background: #1e293b; border-radius: 16px 16px 0 0; width: 100%; max-width: 600px;
    max-height: 85vh; overflow-y: auto; padding: 20px; padding-bottom: 40px;
  }
  .modal .handle { width: 40px; height: 4px; background: #475569; border-radius: 2px; margin: 0 auto 16px; }
  .modal h3 { font-size: 18px; margin-bottom: 12px; }
  .modal .field { margin-bottom: 12px; }
  .modal .field-label { font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
  .modal .field-value { font-size: 14px; color: #cbd5e1; line-height: 1.5; }
  .modal .field-value a { color: #38bdf8; text-decoration: none; }
  .modal .field-value pre { background: #0f172a; padding: 10px; border-radius: 8px; overflow-x: auto; font-size: 12px; white-space: pre-wrap; word-break: break-word; }

  .empty-state { text-align: center; padding: 40px 20px; color: #475569; }
  .empty-state .icon { font-size: 40px; margin-bottom: 12px; }
  .empty-state p { font-size: 14px; }

  .refresh-btn {
    background: none; border: 1px solid #334155; color: #64748b; padding: 8px 14px;
    border-radius: 8px; font-size: 13px; cursor: pointer; margin-bottom: 12px;
    width: 100%;
  }
  .refresh-btn:active { background: #1e293b; }
</style>
</head>
<body>

<!-- Toast -->
<div id="toast" class="toast"></div>

<!-- Header -->
<div class="header">
  <h1>🤖 TrendyReports Agent</h1>
  <div class="sub">Autonomous Development Orchestrator</div>
</div>

<!-- Tabs -->
<div class="tabs">
  <div class="tab active" data-tab="new">✨ New Task</div>
  <div class="tab" data-tab="log">📋 Log <span class="badge" id="log-badge" style="display:none">0</span></div>
</div>

<!-- ═══ New Task Tab ═══ -->
<div class="content">
<div id="panel-new" class="tab-panel active">

  <form id="taskForm">
    <label>What needs to happen?</label>
    <input type="text" id="title" placeholder="e.g. Add sqft column to CMA table" required>

    <label>Details</label>
    <textarea id="description" placeholder="Describe what you want built, changed, or fixed..."></textarea>

    <div class="row">
      <div>
        <label>Priority</label>
        <select id="priority">
          <option value="medium" selected>🔵 Medium</option>
          <option value="low">⚪ Low</option>
          <option value="high">🟠 High</option>
          <option value="urgent">🔴 Urgent</option>
        </select>
      </div>
      <div>
        <label>Trust Level</label>
        <select id="trust_level">
          <option value="full_auto" selected>🚀 Full Auto</option>
          <option value="preview_only">👀 Preview Only</option>
          <option value="plan_only">📋 Plan Only</option>
        </select>
      </div>
    </div>

    <label>Extra Context <span style="font-weight:400;color:#475569">(optional)</span></label>
    <textarea id="context" style="min-height:60px" placeholder="Links, file paths, references..."></textarea>

    <button type="submit" class="primary" id="submitBtn">🚀 Queue Task</button>
  </form>

</div>

<!-- ═══ Log Tab ═══ -->
<div id="panel-log" class="tab-panel">

  <div id="stats-bar" class="stats-bar"></div>
  <button class="refresh-btn" onclick="loadTasks()">↻ Refresh</button>
  <div id="task-list" class="task-list"></div>

</div>
</div>

<!-- Task Detail Modal -->
<div class="modal-overlay" id="modal" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <div class="handle"></div>
    <div id="modal-content"></div>
  </div>
</div>

<script>
// ── Auth ──
const SECRET = localStorage.getItem('intake_secret') || prompt('Enter intake secret:');
if (SECRET) localStorage.setItem('intake_secret', SECRET);

const headers = { 'Authorization': `Bearer ${SECRET}`, 'Content-Type': 'application/json' };

// ── Tabs ──
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('panel-' + tab.dataset.tab).classList.add('active');
    if (tab.dataset.tab === 'log') loadTasks();
  });
});

// ── Toast ──
function showToast(msg, type = 'success') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast ' + type + ' show';
  setTimeout(() => t.classList.remove('show'), 3000);
}

// ── Submit task ──
document.getElementById('taskForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('submitBtn');
  btn.disabled = true;
  btn.textContent = 'Queuing...';

  try {
    const body = {
      title: document.getElementById('title').value,
      description: document.getElementById('description').value,
      priority: document.getElementById('priority').value,
      trust_level: document.getElementById('trust_level').value,
      context: document.getElementById('context').value,
    };
    const res = await fetch('/task', { method: 'POST', headers, body: JSON.stringify(body) });
    if (res.ok) {
      showToast('✅ "' + body.title + '" queued!');
      document.getElementById('taskForm').reset();
      loadStats();
    } else {
      const data = await res.json();
      throw new Error(data.error || 'Failed');
    }
  } catch (err) {
    showToast('❌ ' + err.message, 'error');
  }
  btn.disabled = false;
  btn.textContent = '🚀 Queue Task';
});

// ── Load stats ──
async function loadStats() {
  try {
    const res = await fetch('/stats', { headers });
    const d = await res.json();
    document.getElementById('stats-bar').innerHTML = `
      <div class="stat-card queued"><div class="val">${d.queued}</div><div class="lbl">Queued</div></div>
      <div class="stat-card progress"><div class="val">${d.in_progress}</div><div class="lbl">Working</div></div>
      <div class="stat-card done"><div class="val">${d.completed}</div><div class="lbl">Done</div></div>
      <div class="stat-card failed"><div class="val">${d.failed}</div><div class="lbl">Failed</div></div>
    `;
    const active = d.queued + d.in_progress;
    const badge = document.getElementById('log-badge');
    if (active > 0) { badge.textContent = active; badge.style.display = 'inline'; }
    else { badge.style.display = 'none'; }
  } catch(e) {}
}

// ── Load task list ──
async function loadTasks() {
  loadStats();
  const list = document.getElementById('task-list');
  try {
    const res = await fetch('/tasks', { headers });
    const tasks = await res.json();
    if (!tasks.length) {
      list.innerHTML = '<div class="empty-state"><div class="icon">📭</div><p>No tasks yet. Create your first one!</p></div>';
      return;
    }
    list.innerHTML = tasks.map(t => `
      <div class="task-card" onclick="openTask('${t.id}')">
        <div class="top">
          <span class="pill ${t.status}">${statusLabel(t.status)}</span>
          <span style="font-size:12px;color:#475569">${timeAgo(t.created_at)}</span>
        </div>
        <div class="title"><span class="priority-dot ${t.priority}"></span>${esc(t.title)}</div>
        <div class="meta">
          ${t.pr_url ? '🔗 PR created' : ''}
          ${t.trust_level ? t.trust_level.replace('_', ' ') : ''}
        </div>
      </div>
    `).join('');
  } catch(e) {
    list.innerHTML = '<div class="empty-state"><p>Failed to load tasks</p></div>';
  }
}

// ── Open task detail ──
async function openTask(id) {
  try {
    const res = await fetch('/tasks/' + id, { headers });
    const t = await res.json();
    document.getElementById('modal-content').innerHTML = `
      <h3>${esc(t.title)}</h3>
      <div class="field">
        <div class="field-label">Status</div>
        <div class="field-value"><span class="pill ${t.status}">${statusLabel(t.status)}</span></div>
      </div>
      ${t.description ? `<div class="field"><div class="field-label">Description</div><div class="field-value">${esc(t.description)}</div></div>` : ''}
      ${t.context ? `<div class="field"><div class="field-label">Context</div><div class="field-value">${esc(t.context)}</div></div>` : ''}
      <div class="field">
        <div class="field-label">Priority / Trust</div>
        <div class="field-value"><span class="priority-dot ${t.priority}"></span>${t.priority} · ${(t.trust_level||'').replace('_',' ')}</div>
      </div>
      ${t.pr_url ? `<div class="field"><div class="field-label">Pull Request</div><div class="field-value"><a href="${t.pr_url}" target="_blank">${t.pr_url}</a></div></div>` : ''}
      ${t.preview_url ? `<div class="field"><div class="field-label">Preview</div><div class="field-value"><a href="${t.preview_url}" target="_blank">View Preview</a></div></div>` : ''}
      ${t.error_message ? `<div class="field"><div class="field-label">Error</div><div class="field-value" style="color:#f87171">${esc(t.error_message)}</div></div>` : ''}
      ${t.files_changed ? `<div class="field"><div class="field-label">Files Changed</div><div class="field-value"><pre>${JSON.stringify(t.files_changed, null, 2)}</pre></div></div>` : ''}
      ${t.agent_log ? `<div class="field"><div class="field-label">Agent Log</div><div class="field-value"><pre>${JSON.stringify(t.agent_log, null, 2)}</pre></div></div>` : ''}
      <div class="field">
        <div class="field-label">Created</div>
        <div class="field-value">${new Date(t.created_at).toLocaleString()}</div>
      </div>
      ${t.actual_duration_seconds ? `<div class="field"><div class="field-label">Duration</div><div class="field-value">${Math.round(t.actual_duration_seconds/60)}m ${t.actual_duration_seconds%60}s</div></div>` : ''}
    `;
    document.getElementById('modal').classList.add('open');
  } catch(e) {
    showToast('Failed to load task details', 'error');
  }
}

function closeModal() { document.getElementById('modal').classList.remove('open'); }

// ── Helpers ──
function statusLabel(s) {
  const map = { queued:'Queued', planning:'Planning', coding:'Coding', reviewing:'Reviewing',
    testing:'Testing', deploying:'Deploying', done:'Done ✅', failed:'Failed',
    pr_open:'PR Open' };
  return map[s] || s;
}

function timeAgo(dateStr) {
  const d = new Date(dateStr);
  const now = new Date();
  const diff = Math.floor((now - d) / 1000);
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff/60) + 'm ago';
  if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
  return Math.floor(diff/86400) + 'd ago';
}

function esc(s) { if (!s) return ''; const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

// ── Init ──
loadStats();
setInterval(loadStats, 30000);
</script>
</body>
</html>"""


class IntakeHandler(BaseHTTPRequestHandler):
    def _check_auth(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {settings.intake_secret}":
            return True
        self._respond(401, {"error": "Unauthorized"})
        return False

    def _respond(self, status: int, body, content_type: str = "application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        if isinstance(body, str):
            self.wfile.write(body.encode())
        elif isinstance(body, dict) or isinstance(body, list):
            self.wfile.write(json.dumps(body, default=str).encode())

    def do_OPTIONS(self):
        self._respond(200, "")

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(MOBILE_UI_HTML.encode())
            return

        if parsed.path == "/health":
            self._respond(200, {"status": "ok"})
            return

        if not self._check_auth():
            return

        if parsed.path == "/stats":
            stats = db.get_daily_stats()
            self._respond(200, stats)
            return

        if parsed.path == "/tasks":
            with db.db_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, title, status, priority, trust_level, pr_url, "
                        "created_at, actual_duration_seconds "
                        "FROM agent_tasks ORDER BY created_at DESC LIMIT 50"
                    )
                    tasks = [dict(r) for r in cur.fetchall()]
            self._respond(200, tasks)
            return

        if parsed.path.startswith("/tasks/"):
            task_id = parsed.path.split("/")[-1]
            try:
                task = db.get_task(uuid.UUID(task_id))
                if task:
                    self._respond(200, task)
                else:
                    self._respond(404, {"error": "Not found"})
            except ValueError:
                self._respond(400, {"error": "Invalid UUID"})
            return

        self._respond(404, {"error": "Not found"})

    def do_POST(self):
        if self.path != "/task":
            self._respond(404, {"error": "Not found"})
            return

        if not self._check_auth():
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}

        title = body.get("title", "").strip()
        if not title:
            self._respond(400, {"error": "title is required"})
            return

        task = db.create_task(
            title=title,
            description=body.get("description", ""),
            context=body.get("context", ""),
            trust_level=body.get("trust_level", "full_auto"),
            priority=body.get("priority", "medium"),
        )

        logger.info(f"Task created: {task['id']} — {title}")
        self._respond(201, {"id": str(task["id"]), "title": title, "status": "queued"})

    def log_message(self, format, *args):
        """Suppress default access logs for cleaner output."""
        pass


def main():
    port = settings.intake_port
    server = HTTPServer(("0.0.0.0", port), IntakeHandler)
    logger.info(f"Intake server running on http://0.0.0.0:{port}")
    logger.info(f"Open on your phone: http://YOUR_SERVER_IP:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down intake server...")
        server.server_close()


if __name__ == "__main__":
    main()
