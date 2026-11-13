"""Trace explorer server: a read-only local web UI over a trace file.

Standard library only, one embedded HTML page, no build step. Endpoints:

  GET /              the page
  GET /api/runs      run summaries (id, task, mode, outcome, duration)
  GET /api/runs/<id> one run as an ordered, display-ready event timeline

The server only ever reads the trace file. It holds no state and offers
no mutating route.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import data as explorer_data

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Aegis Trace Explorer</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
         background: #ffffff; color: #1a1a1a; }
  header { padding: 18px 24px 10px; border-bottom: 1px solid #e5e7eb; }
  header h1 { margin: 0; font-size: 22px; }
  header p { margin: 4px 0 0; color: #555; font-size: 13px; }
  main { display: flex; align-items: flex-start; }
  aside { width: 350px; flex: none; border-right: 1px solid #e5e7eb;
          height: calc(100vh - 84px); overflow-y: auto; }
  section { flex: 1; padding: 16px 24px; height: calc(100vh - 84px); overflow-y: auto; }
  .run { padding: 10px 14px; border-bottom: 1px solid #f0f0f0; cursor: pointer; }
  .run:hover { background: #f8fafc; }
  .run.selected { background: #eff6ff; }
  .run .task { font-weight: 600; font-size: 14px; }
  .run .meta { font-size: 12px; color: #666; margin-top: 2px; }
  .badge { display: inline-block; padding: 1px 7px; border-radius: 9px;
           font-size: 11px; font-weight: 600; margin-right: 5px; }
  .b-done { background: #dcfce7; color: #166534; }
  .b-failed { background: #fee2e2; color: #991b1b; }
  .b-other { background: #e5e7eb; color: #374151; }
  .b-mode { background: #e0e7ff; color: #3730a3; }
  .b-scope { background: #f3f4f6; color: #374151; border: 1px solid #d1d5db; }
  .b-gated { background: #fee2e2; color: #991b1b; }
  h2 { font-size: 17px; margin: 2px 0 4px; }
  .sub { color: #555; font-size: 13px; margin-bottom: 14px; }
  .event { border-left: 4px solid #d1d5db; background: #fafafa;
           padding: 8px 12px; margin: 0 0 8px; border-radius: 0 6px 6px 0; }
  .event .title { font-weight: 600; font-size: 13.5px; }
  .event .detail { font-size: 12.5px; color: #444; margin-top: 2px;
                   word-break: break-word; white-space: pre-wrap; }
  .event .when { font-size: 11px; color: #888; }
  .event.model { border-left-color: #2563eb; }
  .event.verify { border-left-color: #16a34a; }
  .event.run { border-left-color: #6b7280; background: #f3f4f6; }
  .event.recovery { border-left-color: #d97706; background: #fffbeb; }
  .event.gate { border-left-color: #dc2626; background: #fef2f2; }
  .empty { color: #777; padding: 20px; }
</style>
</head>
<body>
<header>
  <h1>Aegis Trace Explorer</h1>
  <p>Read-only view over the JSONL trace store. Recovery events are highlighted
     in amber; permission-gate refusals in red. Source file: <span id="src"></span></p>
</header>
<main>
  <aside id="runs"><div class="empty">Loading runs...</div></aside>
  <section id="detail"><div class="empty">Select a run to see its timeline.</div></section>
</main>
<script>
function badge(text, cls) { return '<span class="badge ' + cls + '">' + text + '</span>'; }
function esc(s) { var d = document.createElement('div'); d.textContent = String(s == null ? '' : s); return d.innerHTML; }

async function loadRuns() {
  const res = await fetch('/api/runs');
  const payload = await res.json();
  document.getElementById('src').textContent = payload.trace_file;
  const box = document.getElementById('runs');
  box.innerHTML = '';
  payload.runs.forEach(function(run, i) {
    const div = document.createElement('div');
    div.className = 'run';
    div.dataset.runId = run.run_id;
    const outcomeCls = run.outcome === 'done' ? 'b-done' : (run.outcome === 'failed' ? 'b-failed' : 'b-other');
    div.innerHTML =
      '<div class="task">' + esc(run.task_id) + '</div>' +
      '<div class="meta">' + badge(run.outcome, outcomeCls) +
      badge('recovery ' + run.mode, 'b-mode') +
      esc(run.duration_s) + ' s | ' + esc(run.event_count) + ' events' +
      (run.recovery_events ? ' | ' + esc(run.recovery_events) + ' recovery' : '') +
      (run.gate_events ? ' | ' + esc(run.gate_events) + ' gate' : '') +
      '</div><div class="meta">' + esc(run.started_at) + ' | run ' + esc(run.run_id) + '</div>';
    div.onclick = function() { selectRun(run.run_id, div); };
    box.appendChild(div);
    if (i === 0) { selectRun(run.run_id, div); }
  });
  if (!payload.runs.length) { box.innerHTML = '<div class="empty">No runs in this trace file yet.</div>'; }
}

async function selectRun(runId, el) {
  document.querySelectorAll('.run').forEach(function(n) { n.classList.remove('selected'); });
  if (el) { el.classList.add('selected'); }
  const res = await fetch('/api/runs/' + runId);
  const detailBox = document.getElementById('detail');
  if (!res.ok) { detailBox.innerHTML = '<div class="empty">Run not found.</div>'; return; }
  const payload = await res.json();
  const s = payload.summary;
  let html = '<h2>' + esc(s.task_id) + '</h2>' +
    '<div class="sub">' + esc(s.task_text) + '<br>' +
    badge(s.outcome, s.outcome === 'done' ? 'b-done' : 'b-failed') +
    badge('recovery ' + s.mode, 'b-mode') +
    'started ' + esc(s.started_at) + ' | duration ' + esc(s.duration_s) + ' s | run ' + esc(s.run_id) + '</div>';
  payload.events.forEach(function(ev) {
    html += '<div class="event ' + esc(ev.category) + '">' +
      '<div class="title">' + esc(ev.title) +
      ev.badges.map(function(b) {
        return ' ' + badge(b, b.indexOf('gated') >= 0 ? 'b-gated' : 'b-scope');
      }).join('') + '</div>' +
      (ev.detail ? '<div class="detail">' + esc(ev.detail) + '</div>' : '') +
      '<div class="when">#' + esc(ev.seq) + ' | ' + esc(ev.time) + '</div></div>';
  });
  detailBox.innerHTML = html;
}

loadRuns();
</script>
</body>
</html>
"""


def make_handler(trace_path: Path, manifest: dict[str, Any], scopes: dict[str, Any]):
    class ExplorerHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass  # keep the console clean; this is a local reading tool

        def _send_json(self, obj: Any, status: int = 200) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 (stdlib handler API)
            if self.path == "/":
                body = PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/api/runs":
                self._send_json(
                    {
                        "trace_file": trace_path.name,
                        "runs": explorer_data.list_runs(trace_path, manifest),
                    }
                )
                return
            if self.path.startswith("/api/runs/"):
                run_id = self.path[len("/api/runs/"):].strip("/")
                detail = explorer_data.load_run_detail(
                    trace_path, run_id, manifest, scopes
                )
                if detail is None:
                    self._send_json({"error": "run not found"}, status=404)
                else:
                    self._send_json(detail)
                return
            self._send_json({"error": "not found"}, status=404)

        def _refuse(self) -> None:
            self._send_json({"error": "read-only"}, status=405)

        do_POST = _refuse
        do_PUT = _refuse
        do_DELETE = _refuse
        do_PATCH = _refuse

    return ExplorerHandler


def serve(trace_path: str | Path, port: int = 8765) -> ThreadingHTTPServer:
    trace_path = Path(trace_path)
    manifest = explorer_data.load_manifest(trace_path)
    scopes = explorer_data.tool_scopes()
    server = ThreadingHTTPServer(
        ("127.0.0.1", port), make_handler(trace_path, manifest, scopes)
    )
    return server
