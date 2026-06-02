"""Bank-manager agent UI -- single (unguarded) and side-by-side (vs ACS) modes.

Run:
    python examples/bank_manager_agent_control/unguarded_ui.py

Then open http://127.0.0.1:8766

This UI calls the same callables that the eval suite scores
(``chat_unguarded`` and ``chat_guarded_acs`` in ``agent.py``), so the live
chat behaviour matches the variants rendered by the viewer.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _ensure_opa_on_path() -> None:
    """Make sure the ``opa`` binary is discoverable for the ACS compare pane.

    OPA is the policy engine that ACS uses to evaluate the bundled Rego
    policy at every intervention point. If ``opa`` is already on PATH
    (typical when installed via brew/apt/winget shims, or downloaded
    manually) this is a no-op.

    On Windows, the WinGet installer stores binaries under a per-package
    directory that is *not* automatically added to PATH. We scan
    ``%LOCALAPPDATA%\\Microsoft\\WinGet\\Packages\\`` for any
    ``open-policy-agent.opa_*`` directory and prepend it. This is safe to
    run on any user account -- it never depends on a hardcoded username.
    """
    if shutil.which("opa"):
        return
    if sys.platform != "win32":
        return  # rely on the user's package manager elsewhere
    winget_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if not winget_root.is_dir():
        return
    for pkg_dir in winget_root.glob("open-policy-agent.opa_*"):
        if any(pkg_dir.glob("opa*.exe")):
            os.environ["PATH"] = str(pkg_dir) + os.pathsep + os.environ.get("PATH", "")
            return


_ensure_opa_on_path()

from examples.bank_manager_agent_control.agent import (  # noqa: E402
    _run_unguarded_async,
    _run_agent_async_acs,
)

app = FastAPI()


HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>ResponsibleAI Bank -- Manager Console</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0d1117;
    --surface: #151b24;
    --surface-2: #1c2330;
    --ink: #e6ebf2;
    --ink-soft: #b0b8c4;
    --ink-mute: #6e7888;
    --line: #232b38;
    --line-strong: #2f3a4a;
    --brand: #6aa7ff;
    --brand-deep: #3b78d8;
    --danger: #e07866;
    --danger-soft: rgba(224,120,102,.12);
    --user-bubble: #1f3556;
    --radius: 14px;
    --radius-sm: 8px;
    --shadow-sm: 0 1px 2px rgba(0,0,0,.3);
    --shadow-md: 0 10px 30px rgba(0,0,0,.4), 0 2px 6px rgba(0,0,0,.3);
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; height: 100%; }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg); color: var(--ink);
    font-size: 15px; line-height: 1.55;
    -webkit-font-smoothing: antialiased;
    display: flex; flex-direction: column; height: 100vh;
  }

  /* ---- header ---- */
  header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 32px;
    background: var(--surface);
    border-bottom: 1px solid var(--line);
    flex-shrink: 0;
    gap: 16px;
  }
  .brand { display: flex; align-items: center; gap: 12px; }
  .brand-mark {
    width: 34px; height: 34px; border-radius: 9px;
    background: linear-gradient(135deg, var(--brand), var(--brand-deep));
    color: #0d1117; display: grid; place-items: center;
    font-weight: 700; font-size: 15px; letter-spacing: .02em;
  }
  .brand-text { display: flex; flex-direction: column; line-height: 1.1; }
  .brand-text .name { font-weight: 600; font-size: 15px; letter-spacing: -.005em; }
  .brand-text .sub { font-size: 11px; color: var(--ink-mute); letter-spacing: .08em; text-transform: uppercase; margin-top: 2px; }

  .toggle {
    display: inline-flex;
    padding: 3px;
    background: var(--surface-2);
    border: 1px solid var(--line);
    border-radius: 10px;
  }
  .toggle button {
    border: 0; background: transparent;
    color: var(--ink-mute);
    font: inherit; font-size: 13px; font-weight: 500;
    padding: 6px 14px; border-radius: 7px;
    cursor: pointer;
    transition: background .12s, color .12s;
    display: inline-flex; align-items: center; gap: 6px;
  }
  .toggle button:hover:not(.active) { color: var(--ink-soft); }
  .toggle button.active {
    background: var(--brand);
    color: #0d1117;
    font-weight: 600;
  }
  .toggle svg { width: 14px; height: 14px; }

  /* ---- single view ---- */
  main.single {
    flex: 1; overflow-y: auto; padding: 32px 24px 24px;
  }
  main.single .stream {
    max-width: 760px; margin: 0 auto;
    display: flex; flex-direction: column; gap: 22px;
  }
  .welcome { text-align: center; padding: 64px 20px 32px; color: var(--ink-soft); }
  .welcome h2 {
    font-size: 26px; font-weight: 600; color: var(--ink);
    letter-spacing: -.02em; margin: 0 0 8px;
  }
  .welcome p { margin: 0; font-size: 14px; color: var(--ink-mute); }

  /* ---- compare view ---- */
  .cols {
    flex: 1;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1px;
    background: var(--line);
    overflow: hidden;
    min-height: 0;
  }
  .col {
    background: var(--bg);
    display: flex; flex-direction: column;
    min-height: 0; overflow: hidden;
  }
  .col-head {
    padding: 14px 24px;
    background: var(--surface);
    border-bottom: 1px solid var(--line);
    display: flex; align-items: center; gap: 10px;
    flex-shrink: 0;
  }
  .col-head .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .col.unguarded .col-head .dot {
    background: var(--danger);
    box-shadow: 0 0 0 3px rgba(224,120,102,.18);
  }
  .col.guarded .col-head .dot {
    background: var(--brand);
    box-shadow: 0 0 0 3px rgba(106,167,255,.18);
  }
  .col-head .label {
    font-size: 11px; letter-spacing: .1em;
    text-transform: uppercase; font-weight: 600;
  }
  .col.unguarded .col-head .label { color: var(--danger); }
  .col.guarded .col-head .label { color: var(--brand); }
  .col-head .sub { font-size: 12px; color: var(--ink-mute); margin-left: auto; }

  .col-body { flex: 1; overflow-y: auto; padding: 24px 24px 16px; }
  .col-body .stream {
    display: flex; flex-direction: column; gap: 18px;
    max-width: 640px; margin: 0 auto;
  }
  .empty {
    text-align: center; color: var(--ink-mute);
    padding: 64px 12px 24px; font-size: 13px;
  }

  /* hide whichever view is inactive */
  body[data-mode="single"] .cols { display: none; }
  body[data-mode="compare"] main.single { display: none; }

  /* ---- messages (shared) ---- */
  .turn { display: flex; flex-direction: column; gap: 6px; }
  .turn.user { align-items: flex-end; }
  .turn.bot { align-items: flex-start; }
  .who {
    font-size: 11px; color: var(--ink-mute);
    letter-spacing: .08em; text-transform: uppercase;
    font-weight: 500; padding: 0 4px;
  }
  .bubble {
    max-width: 90%; padding: 12px 16px;
    border-radius: var(--radius);
    white-space: pre-wrap; word-wrap: break-word;
    font-size: 14.5px; line-height: 1.55;
  }
  .turn.user .bubble {
    background: var(--user-bubble); color: var(--ink);
    border: 1px solid var(--line-strong);
    border-bottom-right-radius: 4px;
  }
  .turn.bot .bubble {
    background: var(--surface); color: var(--ink);
    border: 1px solid var(--line);
    border-bottom-left-radius: 4px;
    box-shadow: var(--shadow-sm);
  }
  .turn.bot .bubble.loading { color: var(--ink-mute); }
  .turn.bot .bubble.error {
    background: var(--danger-soft);
    border-color: rgba(224,120,102,.35);
    color: var(--danger);
  }
  .col-body .bubble { font-size: 14px; padding: 11px 14px; max-width: 92%; }

  .typing { display: inline-flex; gap: 4px; align-items: center; }
  .typing span {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--ink-mute);
    animation: blink 1.2s infinite ease-in-out;
  }
  .typing span:nth-child(2) { animation-delay: .15s; }
  .typing span:nth-child(3) { animation-delay: .3s; }
  @keyframes blink {
    0%, 80%, 100% { opacity: .25; transform: translateY(0); }
    40% { opacity: 1; transform: translateY(-2px); }
  }

  .meta {
    font-size: 11px; color: var(--ink-mute);
    padding: 0 4px; font-family: 'JetBrains Mono', monospace;
  }

  /* ---- composer ---- */
  footer {
    padding: 16px 24px 22px;
    background: linear-gradient(to bottom, transparent, var(--bg) 30%);
    flex-shrink: 0;
  }
  .composer-wrap { max-width: 980px; margin: 0 auto; }
  body[data-mode="single"] .composer-wrap { max-width: 760px; }
  .composer {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    box-shadow: var(--shadow-md);
    padding: 6px 6px 6px 14px;
    display: flex; align-items: flex-end; gap: 8px;
    transition: border-color .12s, box-shadow .12s;
  }
  .composer:focus-within {
    border-color: var(--brand);
    box-shadow: var(--shadow-md), 0 0 0 3px rgba(106,167,255,.14);
  }
  #msg {
    flex: 1; border: 0; outline: 0; background: transparent;
    resize: none; font: inherit; font-size: 14.5px;
    color: var(--ink); padding: 10px 0;
    max-height: 200px; min-height: 24px; line-height: 1.5;
  }
  #msg::placeholder { color: var(--ink-mute); }

  #send {
    border: 0;
    background: linear-gradient(135deg, var(--brand), var(--brand-deep));
    color: #0d1117;
    width: 38px; height: 38px; border-radius: 10px;
    display: grid; place-items: center;
    cursor: pointer; flex-shrink: 0;
    transition: filter .12s, transform .08s;
  }
  #send:hover:not(:disabled) { filter: brightness(1.1); }
  #send:active:not(:disabled) { transform: translateY(1px); }
  #send:disabled { background: var(--line-strong); color: var(--ink-mute); cursor: not-allowed; }
  #send svg { width: 16px; height: 16px; }

  .hint {
    margin: 8px auto 0;
    font-size: 11px; color: var(--ink-mute);
    text-align: center; letter-spacing: .02em;
  }
  .hint kbd {
    font-family: 'JetBrains Mono', monospace;
    background: var(--surface); color: var(--ink-soft);
    border: 1px solid var(--line);
    border-radius: 4px;
    padding: 1px 5px; font-size: 10px;
  }

  main.single::-webkit-scrollbar, .col-body::-webkit-scrollbar { width: 10px; }
  main.single::-webkit-scrollbar-thumb, .col-body::-webkit-scrollbar-thumb {
    background: var(--line-strong); border-radius: 10px; border: 2px solid var(--bg);
  }
  main.single::-webkit-scrollbar-track, .col-body::-webkit-scrollbar-track { background: transparent; }

  @media (max-width: 820px) {
    header { padding: 12px 18px; flex-wrap: wrap; }
    .cols { grid-template-columns: 1fr; grid-template-rows: 1fr 1fr; }
    .toggle button { padding: 6px 10px; font-size: 12px; }
  }
</style>
</head>
<body data-mode="single">

<header>
  <div class="brand">
    <div class="brand-mark">R</div>
    <div class="brand-text">
      <span class="name">ResponsibleAI Bank</span>
      <span class="sub">Manager Console</span>
    </div>
  </div>
  <div class="toggle" role="tablist" aria-label="View mode">
    <button id="mode-single" class="active" role="tab" aria-selected="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
      Single
    </button>
    <button id="mode-compare" role="tab" aria-selected="false">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="7" height="16" rx="1"/><rect x="14" y="4" width="7" height="16" rx="1"/></svg>
      Compare
    </button>
  </div>
</header>

<!-- Single mode -->
<main class="single">
  <div class="stream" id="single-stream">
    <div class="welcome" id="welcome">
      <h2>Good day. How can I help?</h2>
      <p>Ask the manager about accounts, balances, or transfers.</p>
    </div>
  </div>
</main>

<!-- Compare mode -->
<div class="cols">
  <section class="col unguarded">
    <div class="col-head">
      <span class="dot"></span>
      <span class="label">Unguarded</span>
      <span class="sub">no policy enforcement</span>
    </div>
    <div class="col-body"><div class="stream" id="left">
      <div class="empty">Send a message to compare responses.</div>
    </div></div>
  </section>
  <section class="col guarded">
    <div class="col-head">
      <span class="dot"></span>
      <span class="label">ACS-guarded</span>
      <span class="sub">policy gate active</span>
    </div>
    <div class="col-body"><div class="stream" id="right">
      <div class="empty">Send a message to compare responses.</div>
    </div></div>
  </section>
</div>

<footer>
  <div class="composer-wrap">
    <div class="composer">
      <textarea id="msg" rows="1" placeholder="Message the bank manager…"></textarea>
      <button id="send" title="Send (Ctrl+Enter)" aria-label="Send">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l14-7-7 14-2-5-5-2z"/></svg>
      </button>
    </div>
    <div class="hint"><kbd>Ctrl</kbd> + <kbd>Enter</kbd> to send</div>
  </div>
</footer>

<script>
const send = document.getElementById('send');
const msgEl = document.getElementById('msg');
const singleStream = document.getElementById('single-stream');
const left = document.getElementById('left');
const right = document.getElementById('right');
const welcome = document.getElementById('welcome');
const btnSingle = document.getElementById('mode-single');
const btnCompare = document.getElementById('mode-compare');

let mode = 'single';

function setMode(next) {
  mode = next;
  document.body.dataset.mode = next;
  btnSingle.classList.toggle('active', next === 'single');
  btnCompare.classList.toggle('active', next === 'compare');
  btnSingle.setAttribute('aria-selected', next === 'single');
  btnCompare.setAttribute('aria-selected', next === 'compare');
  msgEl.placeholder = next === 'compare' ? 'Message both agents…' : 'Message the bank manager…';
  msgEl.focus();
}
btnSingle.addEventListener('click', () => setMode('single'));
btnCompare.addEventListener('click', () => setMode('compare'));

function autoSize() {
  msgEl.style.height = 'auto';
  msgEl.style.height = Math.min(msgEl.scrollHeight, 200) + 'px';
}
msgEl.addEventListener('input', autoSize);

function clearEmpty(col) {
  const e = col.querySelector('.empty');
  if (e) e.remove();
}

function addTurn(col, kind, who, text, loading) {
  const t = document.createElement('div');
  t.className = 'turn ' + kind;
  const w = document.createElement('div');
  w.className = 'who';
  w.textContent = who;
  const body = document.createElement('div');
  body.className = 'bubble' + (loading ? ' loading' : '');
  if (loading) {
    body.innerHTML = '<span class="typing"><span></span><span></span><span></span></span>';
  } else {
    body.textContent = text;
  }
  t.appendChild(w);
  t.appendChild(body);
  col.appendChild(t);
  scrollBottom(col);
  return t;
}

function scrollBottom(col) {
  const body = col.closest('.col-body') || col.closest('main.single');
  if (body) body.scrollTop = body.scrollHeight;
}

function renderResp(turnEl, resp) {
  const body = turnEl.querySelector('.bubble');
  body.classList.remove('loading');
  body.innerHTML = '';
  if (resp.error) {
    body.classList.add('error');
    body.textContent = resp.error;
  } else {
    body.textContent = resp.text || '(empty response)';
  }
  const meta = document.createElement('div');
  meta.className = 'meta';
  meta.textContent = (resp.ms / 1000).toFixed(2) + 's';
  turnEl.appendChild(meta);
}

function renderError(turnEl, msg) {
  const body = turnEl.querySelector('.bubble');
  body.classList.remove('loading');
  body.classList.add('error');
  body.innerHTML = '';
  body.textContent = 'Request failed: ' + msg;
}

async function go() {
  const msg = msgEl.value.trim();
  if (!msg) return;
  send.disabled = true;
  msgEl.value = '';
  autoSize();

  if (mode === 'single') {
    if (welcome && welcome.parentNode) welcome.remove();
    addTurn(singleStream, 'user', 'You', msg);
    const pending = addTurn(singleStream, 'bot', 'Bank Manager', '', true);
    try {
      const r = await fetch('/chat', { method: 'POST', headers: {'content-type':'application/json'}, body: JSON.stringify({message: msg})});
      const data = await r.json();
      renderResp(pending, data);
    } catch (e) { renderError(pending, e); }
  } else {
    clearEmpty(left); clearEmpty(right);
    addTurn(left, 'user', 'You', msg);
    addTurn(right, 'user', 'You', msg);
    const lPending = addTurn(left, 'bot', 'Unguarded', '', true);
    const rPending = addTurn(right, 'bot', 'ACS-guarded', '', true);
    try {
      const r = await fetch('/compare', { method: 'POST', headers: {'content-type':'application/json'}, body: JSON.stringify({message: msg})});
      const data = await r.json();
      renderResp(lPending, data.unguarded);
      renderResp(rPending, data.guarded);
    } catch (e) {
      renderError(lPending, e);
      renderError(rPending, e);
    }
  }

  send.disabled = false;
  msgEl.focus();
}

send.addEventListener('click', go);
msgEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); go(); }
});
msgEl.focus();
</script>
</body>
</html>
"""


class ChatRequest(BaseModel):
    message: str


async def _timed(coro):
    t0 = time.monotonic()
    try:
        text = await coro
        return {"text": text, "ms": int((time.monotonic() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}", "ms": int((time.monotonic() - t0) * 1000)}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return HTML


@app.post("/chat")
async def chat(req: ChatRequest):
    return JSONResponse(await _timed(_run_unguarded_async(req.message)))


@app.post("/compare")
async def compare(req: ChatRequest):
    unguarded, guarded = await asyncio.gather(
        _timed(_run_unguarded_async(req.message)),
        _timed(_run_agent_async_acs(req.message)),
    )
    return JSONResponse({"unguarded": unguarded, "guarded": guarded})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8766, log_level="info")
