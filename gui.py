"""
gui.py

DwaniLive's launcher window.

WHY THIS NO LONGER USES PYWEBVIEW
  The previous version rendered through pywebview -> pythonnet -> WebView2.
  That chain is the single most fragile part of a frozen Windows build:
  pywebview's backend is imported dynamically (PyInstaller/Nuitka miss it),
  pythonnet needs Python.Runtime.dll + clr_loader shipped exactly right,
  and WebView2 must exist on the machine. The shipped v1.0.1 zip contained
  none of webview/clr_loader at all -- every user silently got the console
  fallback, or (in a windowed build) nothing.

  Now: a tiny stdlib HTTP server on 127.0.0.1 serves the same branded UI,
  opened as a chromeless app window with Microsoft Edge (`msedge --app=`,
  present on every Windows 10/11 machine), falling back to Chrome, then
  the default browser. Zero extra dependencies, nothing to bundle.

  Security: bound to 127.0.0.1 only, every API call needs a per-launch
  random token (so a random website can't POST to it), and the HTML
  is served from the same origin.

LIFECYCLE
  The window polls /api/state every second. Closing it sends a beacon; if
  no new poll arrives within a few seconds (i.e. it wasn't just a reload)
  the app shuts down cleanly. If the beacon is lost, a 120s heartbeat
  timeout does the same. Re-launching DwaniLive while it's running just
  re-opens this window (single-instance, see launcher.py).
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional

import appenv
import model_setup
import updater

HEARTBEAT_TIMEOUT_S = 120
CLOSE_GRACE_S = 6

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DwaniLive</title>
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Rozha+One&family=Mukta:wght@400;500;600;700;800&display=swap" media="print" onload="this.media='all'">
<style>
  :root {
    --bg: #fdf6e7;
    --bg-raised: #fffdf6;
    --bg-raised-2: #f4e9cf;
    --ink: #2c1810;
    --ink-dim: #5c4433;
    --ink-faint: #83694f;
    --accent: #e2790f;
    --accent-deep: #a8460c;
    --accent-2: #c81e5e;
    --accent-3: #0d6e5c;
    --indigo: #223367;
    --live: #2f7a3a;
    --error: #b3261e;
    --shadow: 0 1px 2px rgba(44, 24, 16, 0.08), 0 10px 26px -8px rgba(44, 24, 16, 0.20);
    --font-display: "Rozha One", "Noto Serif Devanagari", Georgia, serif;
    --font-body: "Mukta", "Noto Sans", "Segoe UI", -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; height: 100%;
    background: var(--bg);
    color: var(--ink);
    font-family: var(--font-body);
    line-height: 1.5;
    overflow: hidden; /* this is an app window, not a scrolling page */
  }
  body {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 1.75rem 1.5rem 1.25rem;
  }
  .brand-name {
    display: inline-flex; align-items: baseline; gap: 0.08em;
    font-family: var(--font-display); font-size: 1.9rem; font-weight: 400;
    letter-spacing: -0.01em; margin-bottom: 1.25rem; flex-shrink: 0;
  }
  .brand-name .dwani {
    background: linear-gradient(120deg, var(--accent-deep), var(--accent-2), var(--indigo));
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }
  .brand-name .live {
    color: var(--ink-dim); font-family: var(--font-body); font-size: 0.36em;
    font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase;
  }
  .card {
    width: 100%; max-width: 380px; flex: 1; min-height: 0;
    display: flex; flex-direction: column;
    background: var(--bg-raised); border-radius: 16px; box-shadow: var(--shadow);
    padding: 1.75rem 1.75rem 1.5rem; overflow: hidden;
  }
  .view { display: none; flex-direction: column; height: 100%; min-height: 0; }
  .view.active { display: flex; }
  h1 { font-family: var(--font-display); font-weight: 400; font-size: 1.4rem; margin: 0 0 0.35rem; }
  .subtitle { color: var(--ink-dim); font-size: 0.9rem; margin: 0 0 1.25rem; }

  /* -- download view -- */
  .progress-track { height: 10px; border-radius: 999px; background: var(--bg-raised-2); overflow: hidden; }
  .progress-fill { height: 100%; width: 0%; border-radius: 999px;
    background: linear-gradient(90deg, var(--accent-deep), var(--accent-2)); transition: width 0.2s ease; }
  .progress-stats { display: flex; justify-content: space-between; margin-top: 0.5rem;
    font-size: 0.8rem; color: var(--ink-faint); font-variant-numeric: tabular-nums; }

  /* -- activate view -- */
  label { display: block; font-size: 0.82rem; font-weight: 600; color: var(--ink-dim); margin: 0.9rem 0 0.3rem; }
  label:first-of-type { margin-top: 0; }
  input[type="text"], input[type="email"] {
    width: 100%; padding: 0.65rem 0.8rem; border-radius: 10px;
    border: 1.5px solid var(--bg-raised-2); background: var(--bg);
    font-family: var(--font-body); font-size: 0.95rem; color: var(--ink);
  }
  input:focus { outline: none; border-color: var(--accent-deep); }
  .error-banner {
    display: none; margin-top: 0.9rem; padding: 0.6rem 0.75rem; border-radius: 8px;
    background: #fbe4e1; color: var(--error); font-size: 0.85rem; white-space: pre-line;
  }
  .error-banner.show { display: block; }
  button.primary {
    margin-top: 1.25rem; width: 100%; padding: 0.75rem; border: none; border-radius: 10px;
    background: var(--accent-deep); color: #fffdf6; font-family: var(--font-body);
    font-weight: 700; font-size: 0.95rem; cursor: pointer;
  }
  button.primary:disabled { opacity: 0.6; cursor: default; }
  button.primary:not(:disabled):hover { background: var(--accent-2); }
  .fine-print { margin-top: 0.9rem; font-size: 0.78rem; color: var(--ink-faint); text-align: center; }

  /* -- starting / spinner -- */
  .spinner-row { display: flex; align-items: center; gap: 0.7rem; margin-bottom: 0.25rem; }
  .spinner {
    width: 20px; height: 20px; border-radius: 50%;
    border: 3px solid var(--bg-raised-2); border-top-color: var(--accent-deep);
    animation: spin 0.8s linear infinite; flex-shrink: 0;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .status-line { font-size: 0.9rem; color: var(--ink-dim); }

  /* -- ready view -- */
  .ok-badge {
    width: 44px; height: 44px; border-radius: 50%; background: var(--live);
    color: #fff; display: flex; align-items: center; justify-content: center;
    font-size: 1.3rem; margin-bottom: 0.75rem;
  }
  .section-label { font-size: 0.78rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.05em; color: var(--ink-faint); margin: 1rem 0 0.4rem; }
  .section-label:first-of-type { margin-top: 0; }
  a.link-button {
    display: block; text-align: center; text-decoration: none;
    padding: 0.7rem; border-radius: 10px; background: var(--accent-deep);
    color: #fffdf6; font-weight: 700; font-size: 0.9rem; cursor: pointer;
  }
  a.link-button:hover { background: var(--accent-2); }

  /* -- shared log panel -- */
  .log-toggle {
    background: none; border: none; color: var(--ink-faint); font-size: 0.78rem;
    cursor: pointer; padding: 0; margin-top: 0.9rem; text-decoration: underline;
    font-family: var(--font-body); align-self: flex-start;
  }
  .log-panel {
    display: none; margin-top: 0.5rem; flex: 1; min-height: 60px;
    background: #2c1810; color: #f4e9cf; border-radius: 8px; padding: 0.6rem 0.7rem;
    font-family: "Consolas", "SFMono-Regular", Menlo, monospace; font-size: 0.72rem;
    overflow-y: auto; white-space: pre-wrap; word-break: break-word;
  }
  .log-panel.show { display: block; }

  /* -- error view -- */
  .error-icon { color: var(--error); font-size: 1.6rem; margin-bottom: 0.5rem; }
  button.secondary {
    margin-top: 1rem; width: 100%; padding: 0.65rem; border-radius: 10px;
    border: 1.5px solid var(--bg-raised-2); background: transparent;
    color: var(--ink-dim); font-family: var(--font-body); font-weight: 600;
    font-size: 0.88rem; cursor: pointer;
  }
  html, body { overflow: auto; }
  .steps { list-style: none; padding: 0; margin: 0 0 1rem; }
  .steps li { display: flex; gap: 0.55rem; align-items: center; font-size: 0.88rem; color: var(--ink-faint); padding: 0.18rem 0; }
  .steps li .dot { width: 18px; height: 18px; border-radius: 50%; border: 2px solid var(--bg-raised-2); flex-shrink: 0;
    display: flex; align-items: center; justify-content: center; font-size: 0.7rem; color: #fff; }
  .steps li.active { color: var(--ink); font-weight: 600; }
  .steps li.active .dot { border-color: var(--accent-deep); border-top-color: transparent; animation: spin 0.8s linear infinite; }
  .steps li.done { color: var(--ink-dim); }
  .steps li.done .dot { background: var(--live); border-color: var(--live); }
  .note { margin-top: 0.6rem; font-size: 0.8rem; color: var(--accent-deep); min-height: 1.2em; }
  .warn { margin: 0 0 0.9rem; padding: 0.55rem 0.7rem; border-radius: 8px; background: #fff1d6; color: #7a4a00; font-size: 0.8rem; }
  .linkish { background: none; border: none; color: var(--accent-deep); font-family: var(--font-body);
    font-size: 0.86rem; font-weight: 600; cursor: pointer; padding: 0; margin-top: 0.9rem; text-decoration: underline; }
  .row { display: flex; gap: 0.5rem; }
  .row > * { flex: 1; }
  .join-box { display: flex; gap: 0.4rem; align-items: center; background: var(--bg); border: 1.5px solid var(--bg-raised-2);
    border-radius: 10px; padding: 0.45rem 0.55rem; font-size: 0.8rem; word-break: break-all; }
  .join-box span { flex: 1; }
  .join-box button { border: none; background: var(--bg-raised-2); border-radius: 6px; padding: 0.3rem 0.55rem;
    font-family: var(--font-body); font-weight: 700; font-size: 0.75rem; cursor: pointer; color: var(--ink); }
  .qr { display: block; margin: 0.6rem auto 0; width: 150px; height: 150px; image-rendering: pixelated; border-radius: 8px; background: #fff; }
  .hint { font-size: 0.76rem; color: var(--ink-faint); margin-top: 0.5rem; }
  button.danger { margin-top: 0.6rem; width: 100%; padding: 0.6rem; border-radius: 10px; border: 1.5px solid #f0c8c3;
    background: transparent; color: var(--error); font-family: var(--font-body); font-weight: 700; font-size: 0.85rem; cursor: pointer; }
  .version { margin-top: 0.6rem; font-size: 0.7rem; color: var(--ink-faint); }
  .update-banner { display: none; width: 100%; max-width: 380px; margin: -0.5rem 0 0.9rem; padding: 0.6rem 0.8rem;
    border-radius: 12px; background: var(--indigo); color: #fffdf6; font-size: 0.84rem; flex-wrap: wrap; gap: 0.5rem; align-items: center; }
  .update-banner.show { display: flex; }
  .update-banner span { flex: 1; min-width: 150px; }
  .update-banner button { border: none; border-radius: 8px; padding: 0.4rem 0.75rem; background: #fffdf6; color: var(--indigo);
    font-family: var(--font-body); font-weight: 800; font-size: 0.8rem; cursor: pointer; }
  .update-banner button:disabled { opacity: 0.7; cursor: default; }
  .update-err { width: 100%; font-size: 0.76rem; color: #ffd6d1; }
</style>
</head>
<body>
<span class="brand-name"><span class="dwani" lang="hi">ध्वनि</span><span class="live">Live</span></span>
<div class="update-banner" id="update-banner">
  <span id="update-text">A new version is available.</span>
  <button type="button" id="update-btn">Update now</button>
  <div class="update-err" id="update-err"></div>
</div>
<div class="card">

  <section class="view active" id="view-splash">
    <div class="spinner-row"><div class="spinner"></div><div class="status-line">Starting…</div></div>
  </section>

  <section class="view" id="view-download">
    <h1>Setting up DwaniLive</h1>
    <p class="subtitle">One-time download (~1.1 GB). If the internet drops, it resumes where it stopped — just leave this open.</p>
    <div id="warnings-dl"></div>
    <ul class="steps">
      <li id="step-whisper"><span class="dot"></span>Speech recognition model</li>
      <li id="step-nllb"><span class="dot"></span>Translation model</li>
      <li id="step-extract"><span class="dot"></span>Unpacking</li>
    </ul>
    <div class="progress-track"><div class="progress-fill" id="progress-fill"></div></div>
    <div class="progress-stats"><span id="progress-pct">0%</span><span id="progress-mb"></span></div>
    <div class="note" id="progress-note"></div>
  </section>

  <section class="view" id="view-activate">
    <h1>Activate DwaniLive</h1>
    <p class="subtitle">Paste the license key from your dashboard. One-time; after this it runs fully offline.</p>
    <div class="error-banner" id="license-problem"></div>
    <form id="activate-form">
      <label for="license-key">License key</label>
      <input type="text" id="license-key" autocomplete="off" spellcheck="false" required>
      <label for="license-email">Email (used at checkout)</label>
      <input type="email" id="license-email" autocomplete="email" required>
      <div class="error-banner" id="activate-error"></div>
      <button type="submit" class="primary" id="activate-btn">Activate</button>
    </form>
    <button class="linkish" id="free-btn" type="button">Continue with the Free plan instead</button>
  </section>

  <section class="view" id="view-starting">
    <h1>Starting DwaniLive…</h1>
    <div id="warnings-st"></div>
    <div class="spinner-row"><div class="spinner"></div><div class="status-line" id="starting-status">Getting things ready…</div></div>
    <p class="hint">Loading the models takes 20–60 seconds on most laptops. If Windows asks about network access, click <b>Allow</b> — that's what lets phones join.</p>
    <button class="log-toggle" data-log="starting-log">Show details</button>
    <div class="log-panel" id="starting-log"></div>
  </section>

  <section class="view" id="view-ready">
    <div class="ok-badge">&#10003;</div>
    <h1>DwaniLive is running</h1>
    <p class="subtitle" id="tier-line">Keep this window open during the session.</p>
    <a href="#" class="link-button" id="open-presenter-btn">Open presenter page ↗</a>
    <div class="section-label">Attendees join here (same Wi-Fi)</div>
    <div class="join-box"><span id="join-url">…</span><button id="copy-join" type="button">Copy</button></div>
    <img class="qr" id="join-qr" alt="Join QR code">
    <p class="hint">Phones can't connect? They must be on the same Wi-Fi. Some college/hotel Wi-Fi blocks phone-to-laptop traffic — use your phone's hotspot for the laptop and attendees instead.</p>
    <button class="danger" id="stop-btn" type="button">Stop session &amp; quit</button>
    <button class="log-toggle" data-log="ready-log">Show details</button>
    <div class="log-panel" id="ready-log"></div>
  </section>

  <section class="view" id="view-error">
    <div class="error-icon">&#9888;</div>
    <h1>Something went wrong</h1>
    <p class="subtitle" id="error-message">Unknown error.</p>
    <button class="primary" id="retry-btn" type="button">Try again</button>
    <div class="row">
      <button class="secondary" id="diag-btn" type="button">Copy error report</button>
      <button class="secondary" id="logs-btn" type="button">Open log folder</button>
    </div>
    <button class="secondary" id="redownload-btn" type="button">Re-download models</button>
    <button class="secondary" id="quit-btn" type="button">Quit</button>
    <button class="log-toggle" data-log="error-log">Show details</button>
    <div class="log-panel" id="error-log"></div>
  </section>

  <div class="version" id="version"></div>
</div>

<script>
(function () {
  var TOKEN = new URLSearchParams(location.search).get('t') || '';
  var seq = 0, phase = '', presenterUrl = '', openedPresenter = false, closing = false;

  function api(path, body) {
    return fetch('/api/' + path + '?t=' + encodeURIComponent(TOKEN), {
      method: body === undefined ? 'GET' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body)
    }).then(function (r) { return r.json(); });
  }
  function $(id) { return document.getElementById(id); }
  function showView(name) {
    document.querySelectorAll('.view').forEach(function (el) {
      el.classList.toggle('active', el.id === 'view-' + name);
    });
  }
  function fmtBytes(b) { return b >= 1073741824 ? (b / 1073741824).toFixed(2) + ' GB' : (b / 1048576).toFixed(0) + ' MB'; }
  function fmtEta(s) {
    if (s === null || s === undefined || !isFinite(s)) return '';
    s = Math.round(s); return s >= 60 ? Math.floor(s / 60) + 'm ' + (s % 60) + 's left' : s + 's left';
  }
  function warningsHtml(list) {
    return (list || []).map(function (w) { var d = document.createElement('div'); d.className = 'warn'; d.textContent = w; return d.outerHTML; }).join('');
  }
  function appendLogs(lines) {
    ['starting-log', 'ready-log', 'error-log'].forEach(function (id) {
      var el = $(id), atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 8;
      lines.forEach(function (l) { el.textContent += l + '\n'; });
      if (el.textContent.length > 60000) el.textContent = el.textContent.slice(-50000);
      if (atBottom) el.scrollTop = el.scrollHeight;
    });
  }
  function setStep(stage) {
    var order = ['whisper', 'nllb', 'extract'], idx = order.indexOf(stage === 'verify' ? 'extract' : stage);
    order.forEach(function (s, i) {
      var li = $('step-' + s);
      li.className = i < idx ? 'done' : (i === idx ? 'active' : '');
    });
  }

  function render(s) {
    $('version').textContent = 'v' + s.version;
    if (s.logs && s.logs.length) appendLogs(s.logs);
    seq = s.seq;
    $('warnings-dl').innerHTML = $('warnings-st').innerHTML = warningsHtml(s.warnings);

    if (s.phase === 'download' && s.progress) {
      var p = s.progress;
      setStep(p.stage);
      $('progress-fill').style.width = p.pct + '%';
      $('progress-pct').textContent = p.total_bytes ? p.pct + '%' : '';
      var mb = p.total_bytes ? fmtBytes(p.done_bytes) + ' / ' + fmtBytes(p.total_bytes) : (p.done_bytes ? fmtBytes(p.done_bytes) : '');
      if (p.speed_bps > 0) mb += '  ·  ' + (p.speed_bps / 1048576).toFixed(1) + ' MB/s  ·  ' + fmtEta(p.eta_s);
      $('progress-mb').textContent = mb;
      $('progress-note').textContent = p.note || p.label || '';
    }
    if (s.phase === 'starting') $('starting-status').textContent = s.status || 'Starting…';
    if (s.phase === 'activate') {
      var lp = $('license-problem');
      lp.textContent = s.license_problem || '';
      lp.classList.toggle('show', !!s.license_problem);
    }
    if (s.phase === 'ready') {
      presenterUrl = s.presenter_url;
      $('join-url').textContent = s.join_url || '(see details)';
      if (s.qr_url && $('join-qr').getAttribute('src') !== s.qr_url) $('join-qr').src = s.qr_url;
      $('tier-line').textContent = (s.tier ? s.tier + ' plan · ' : '') + 'Keep this window open during the session.';
      if (!openedPresenter && s.auto_open) { openedPresenter = true; api('open', { what: 'presenter' }); }
    }
    if (s.phase === 'error') $('error-message').textContent = s.error || 'Unknown error.';
    renderUpdate(s.update);
    if (s.phase !== phase) { phase = s.phase; showView(phase === 'preflight' ? 'splash' : phase); }
  }

  function renderUpdate(u) {
    var b = $('update-banner');
    if (!u) { b.classList.remove('show'); return; }
    b.classList.add('show');
    var btn = $('update-btn');
    if (u.state === 'downloading') {
      $('update-text').textContent = 'Downloading DwaniLive ' + u.version + '… ' + (u.pct || 0) + '%';
      btn.disabled = true; btn.textContent = 'Please wait';
    } else if (u.state === 'installing') {
      $('update-text').textContent = 'Installing ' + u.version + ' — DwaniLive will reopen by itself.';
      btn.disabled = true; btn.textContent = 'Installing…';
    } else {
      $('update-text').textContent = 'DwaniLive ' + u.version + ' is available (you have ' + $('version').textContent + ').';
      btn.disabled = false; btn.textContent = 'Update now';
    }
    $('update-err').textContent = u.error || '';
  }

  function poll() {
    if (closing) return;
    fetch('/api/state?since=' + seq + '&t=' + encodeURIComponent(TOKEN))
      .then(function (r) { return r.json(); }).then(render)
      .catch(function () {}).finally(function () { setTimeout(poll, 1000); });
  }

  document.querySelectorAll('.log-toggle').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var p = $(btn.getAttribute('data-log')), on = p.classList.toggle('show');
      btn.textContent = on ? 'Hide details' : 'Show details';
      if (on) p.scrollTop = p.scrollHeight;
    });
  });
  $('activate-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var btn = $('activate-btn'), err = $('activate-error');
    btn.disabled = true; btn.textContent = 'Activating… (server may take ~30s to wake up)';
    err.classList.remove('show');
    api('activate', { key: $('license-key').value.trim(), email: $('license-email').value.trim() }).then(function (r) {
      if (!r.ok) { err.textContent = r.error || 'Activation failed.'; err.classList.add('show'); }
    }).catch(function () { err.textContent = 'Lost contact with DwaniLive. Reopen the app.'; err.classList.add('show'); })
      .finally(function () { btn.disabled = false; btn.textContent = 'Activate'; });
  });
  $('update-btn').addEventListener('click', function () {
    if (phase === 'ready' && !confirm('Updating will stop the current session. Update now?')) return;
    api('update', {});
  });
  $('free-btn').addEventListener('click', function () { api('continue_free', {}); });
  $('open-presenter-btn').addEventListener('click', function (e) { e.preventDefault(); api('open', { what: 'presenter' }); });
  $('copy-join').addEventListener('click', function () {
    var t = $('join-url').textContent;
    (navigator.clipboard ? navigator.clipboard.writeText(t) : Promise.reject()).then(function () {
      $('copy-join').textContent = 'Copied'; setTimeout(function () { $('copy-join').textContent = 'Copy'; }, 1500);
    }).catch(function () {});
  });
  $('retry-btn').addEventListener('click', function () { api('retry', {}); });
  $('redownload-btn').addEventListener('click', function () {
    if (confirm('Delete the downloaded models and download them again (~1.1 GB)?')) api('retry', { reset_models: true });
  });
  $('logs-btn').addEventListener('click', function () { api('open', { what: 'logs' }); });
  $('diag-btn').addEventListener('click', function () {
    api('diagnostics', {}).then(function (r) {
      var done = function () { $('diag-btn').textContent = 'Copied — paste it to support'; };
      if (navigator.clipboard) navigator.clipboard.writeText(r.text).then(done, function () { api('open', { what: 'logs' }); });
      else api('open', { what: 'logs' });
    });
  });
  function quit() { closing = true; api('quit', {}).finally(function () { document.body.innerHTML = '<p style="padding:2rem;font-family:sans-serif">DwaniLive has stopped. You can close this window.</p>'; window.close(); }); }
  $('quit-btn').addEventListener('click', quit);
  $('stop-btn').addEventListener('click', function () { if (confirm('Stop the session? Attendees will be disconnected.')) quit(); });

  window.addEventListener('beforeunload', function (e) {
    if (phase === 'ready' && !closing) { e.preventDefault(); e.returnValue = ''; }
  });
  window.addEventListener('pagehide', function () {
    if (!closing && navigator.sendBeacon) navigator.sendBeacon('/api/closed?t=' + encodeURIComponent(TOKEN), '{}');
  });
  poll();
})();
</script>
</body>
</html>
"""


class LauncherState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.phase = "preflight"
        self.status = ""
        self.progress: Optional[dict] = None
        self.warnings: list[str] = []
        self.error = ""
        self.license_problem = ""
        self.presenter_url = ""
        self.join_url = ""
        self.server_port: Optional[int] = None
        self.tier = ""
        self.auto_open = False
        self.last_seen = time.monotonic()
        self.closed_at: Optional[float] = None
        self.update: Optional[dict] = None        # {version, notes_url, state, pct, error}

    def set(self, **kw) -> None:
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def to_json(self, since: int) -> dict:
        seq, lines = appenv.LOG.snapshot(since)
        with self.lock:
            qr = f"http://127.0.0.1:{self.server_port}/qr.png" if self.server_port and self.phase == "ready" else ""
            return {
                "phase": self.phase, "status": self.status, "progress": self.progress,
                "warnings": self.warnings, "error": self.error, "license_problem": self.license_problem,
                "presenter_url": self.presenter_url, "join_url": self.join_url, "qr_url": qr,
                "tier": self.tier, "auto_open": self.auto_open, "update": self.update,
                "version": appenv.APP_VERSION, "seq": seq, "logs": lines,
            }


class LauncherApp:
    """Owns the setup pipeline and the control window. launcher.py builds
    one and calls run(); run() blocks until the user quits."""

    def __init__(
        self,
        *,
        setup_firewall_if_needed: Callable[[], None],
        server_port: int,
        license_server_url: str,
        extra_server_args: Optional[list[str]] = None,
    ) -> None:
        self.setup_firewall_if_needed = setup_firewall_if_needed
        self.preferred_port = server_port
        self.license_server_url = license_server_url
        self.extra_server_args = extra_server_args or []
        self.state = LauncherState()
        self.token = secrets.token_urlsafe(18)
        self.ui_url = ""
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._license_decision = threading.Event()
        self._cancel = False
        self._pipeline_thread: Optional[threading.Thread] = None
        self._server_started = False
        self._quit = threading.Event()
        appenv.LOG.add_listener(self._on_log_line)

    # ------------------------------------------------------------------ http
    def _make_handler(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # keep the log panel clean
                pass

            def _authed(self) -> bool:
                from urllib.parse import parse_qs, urlparse

                q = parse_qs(urlparse(self.path).query)
                return secrets.compare_digest((q.get("t") or [""])[0], app.token)

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _json(self, obj, code: int = 200) -> None:
                self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

            def do_GET(self):
                from urllib.parse import parse_qs, urlparse

                u = urlparse(self.path)
                if u.path == "/favicon.png":  # browsers fetch this without our token
                    try:
                        return self._send(200, (appenv.STATIC_DIR / "favicon.png").read_bytes(), "image/png")
                    except OSError:
                        return self._send(404, b"", "text/plain")
                if not self._authed():
                    return self._send(403, b"Forbidden", "text/plain")
                if u.path == "/":
                    return self._send(200, _HTML.encode("utf-8"), "text/html; charset=utf-8")
                if u.path == "/api/state":
                    app.state.set(last_seen=time.monotonic(), closed_at=None)
                    since = int((parse_qs(u.query).get("since") or ["0"])[0] or 0)
                    return self._json(app.state.to_json(since))
                return self._send(404, b"Not found", "text/plain")

            def do_POST(self):
                from urllib.parse import urlparse

                if not self._authed():
                    return self._send(403, b"Forbidden", "text/plain")
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                except ValueError:
                    body = {}
                route = urlparse(self.path).path
                try:
                    return self._json(app.handle_post(route, body))
                except Exception as exc:  # never let a UI action crash the launcher
                    traceback.print_exc()
                    return self._json({"ok": False, "error": appenv.friendly_error(exc)}, 500)

        return Handler

    def handle_post(self, route: str, body: dict) -> dict:
        if route == "/api/activate":
            return self._activate(body.get("key", ""), body.get("email", ""))
        if route == "/api/continue_free":
            self._use_free_plan()
            return {"ok": True}
        if route == "/api/retry":
            self._retry(reset_models=bool(body.get("reset_models")))
            return {"ok": True}
        if route == "/api/open":
            self._open(body.get("what", ""))
            return {"ok": True}
        if route == "/api/diagnostics":
            return {"ok": True, "text": diagnostics_text()}
        if route == "/api/closed":
            self.state.set(closed_at=time.monotonic())
            return {"ok": True}
        if route == "/api/update":
            return self._start_update()
        if route == "/api/quit":
            threading.Timer(0.3, self.shutdown).start()
            return {"ok": True}
        return {"ok": False, "error": "unknown action"}

    # --------------------------------------------------------------- actions
    def _activate(self, key: str, email: str) -> dict:
        if not key or not email:
            return {"ok": False, "error": "Enter both the license key and the email."}
        from activate import activate as do_activate

        mark = appenv.LOG.seq
        ok = do_activate(key, self.license_server_url, email)
        if not ok:
            _, lines = appenv.LOG.snapshot(mark)
            detail = "\n".join(l for l in lines if "ctivat" in l or "->" in l) or "Activation failed. Check the key and email."
            return {"ok": False, "error": detail}
        self.state.set(license_problem="")
        self._license_decision.set()
        return {"ok": True}

    def _use_free_plan(self) -> None:
        from licensing import DEFAULT_CACHE_PATH

        if DEFAULT_CACHE_PATH.exists():
            # An expired/invalid token would make check_license() refuse to
            # start at all; park it so the built-in Free fallback applies.
            try:
                DEFAULT_CACHE_PATH.replace(DEFAULT_CACHE_PATH.with_suffix(".token.inactive"))
            except OSError:
                pass
        self.state.set(license_problem="")
        self._license_decision.set()

    def _retry(self, reset_models: bool = False) -> None:
        if self._server_started:
            # uvicorn can't be restarted in-process cleanly; relaunch ourselves.
            self._relaunch()
            return
        if self._pipeline_thread and self._pipeline_thread.is_alive():
            return
        if reset_models:
            model_setup.reset_models()
        self.state.set(phase="preflight", error="", progress=None)
        self._start_pipeline()

    def _relaunch(self) -> None:
        args = [sys.executable] + ([] if appenv.IS_FROZEN else [str(Path(__file__).resolve().parent / "launcher.py")])
        subprocess.Popen(args + ["--after-restart"], close_fds=True)
        threading.Timer(0.5, self.shutdown).start()

    def _open(self, what: str) -> None:
        if what == "presenter" and self.state.presenter_url:
            webbrowser.open(self.state.presenter_url)
        elif what in ("logs", "data"):
            folder = appenv.LOGS_DIR if what == "logs" else appenv.DATA_DIR
            if sys.platform == "win32":
                os.startfile(str(folder))  # type: ignore[attr-defined]
            else:
                webbrowser.open(folder.as_uri())

    # ---------------------------------------------------------------- update
    def _check_update(self) -> None:
        updater.cleanup_old_installers()
        info = updater.check_for_update()
        if info:
            self._update_info = info
            print(f"Update available: v{info.version}")
            self.state.set(update={"version": info.version, "notes_url": info.notes_url, "state": "available",
                                   "pct": 0, "error": ""})

    def _start_update(self) -> dict:
        info = getattr(self, "_update_info", None)
        if info is None:
            return {"ok": False, "error": "No update available."}
        if sys.platform != "win32":
            webbrowser.open(info.notes_url)
            return {"ok": True}
        upd = dict(self.state.update or {})
        if upd.get("state") == "downloading":
            return {"ok": True}

        def work():
            def prog(p):
                cur = dict(self.state.update or {})
                cur.update(state="downloading", pct=p.pct, error=p.note or "")
                self.state.set(update=cur)
            try:
                self.state.set(update={**upd, "state": "downloading", "pct": 0, "error": ""})
                path = updater.download_installer(info, prog)
                self.state.set(update={**upd, "state": "installing", "pct": 100, "error": ""})
                print(f"Starting installer for v{info.version}; DwaniLive will reopen when it finishes.")
                updater.launch_installer(path)
                threading.Timer(0.8, self.shutdown).start()
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                self.state.set(update={**upd, "state": "available", "pct": 0,
                                       "error": "Update failed: " + appenv.friendly_error(exc)})

        threading.Thread(target=work, name="dwani-update", daemon=True).start()
        return {"ok": True}

    # -------------------------------------------------------------- pipeline
    def _on_log_line(self, line: str) -> None:
        if "Presenter mic page:" in line:
            url = line.split("Presenter mic page:", 1)[1].strip().split()[0]
            self.state.set(presenter_url=url)
            try:
                from urllib.parse import urlparse

                self.state.set(server_port=urlparse(url).port)
            except Exception:
                pass
        elif line.startswith("Join URL:"):
            self.state.set(join_url=line.split(":", 1)[1].strip())

    def _progress(self, p: model_setup.Progress) -> None:
        self.state.set(progress={
            "stage": p.stage, "label": p.label, "done_bytes": p.done_bytes, "total_bytes": p.total_bytes,
            "pct": p.pct, "speed_bps": p.speed_bps, "eta_s": p.eta_s, "note": p.note,
        })

    def _start_pipeline(self) -> None:
        self._pipeline_thread = threading.Thread(target=self._pipeline, name="dwani-setup", daemon=True)
        self._pipeline_thread.start()

    def _fail(self, exc: BaseException) -> None:
        print("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), file=sys.stderr)
        self.state.set(phase="error", error=appenv.friendly_error(exc))

    def _pipeline(self) -> None:
        try:
            self.state.set(phase="preflight")
            self.state.set(warnings=appenv.preflight())

            if not model_setup.all_installed():
                self.state.set(phase="download")
            model_setup.ensure_models(self._progress, cancel=lambda: self._cancel)

            self._license_step()

            self.state.set(phase="starting", status="Setting up local network access…")
            self.setup_firewall_if_needed()

            self.state.set(status="Loading speech + translation models…")
            self._run_server_blocking()
        except BaseException as exc:  # incl. SystemExit from server.main() -- the old GUI missed these and hung forever
            if isinstance(exc, SystemExit) and exc.code in (0, None) and self._quit.is_set():
                return
            if isinstance(exc, SystemExit):
                exc = RuntimeError(str(exc.code) if exc.code not in (None, 1) else _last_meaningful_log_line())
            self._fail(exc)

    def _license_step(self) -> None:
        from licensing import DEFAULT_CACHE_PATH, LicenseError, check_license

        import server

        while True:
            problem = ""
            if DEFAULT_CACHE_PATH.exists():
                try:
                    lic = check_license(server._license_public_key())
                    self.state.set(tier=str(getattr(lic, "tier", "") or "").title())
                    return
                except LicenseError as exc:
                    problem = f"Your saved license can't be used: {exc}"
            elif self._license_decision.is_set():
                self.state.set(tier="Free")
                return
            self._license_decision.clear()
            self.state.set(phase="activate", license_problem=problem)
            self._license_decision.wait()
            if not DEFAULT_CACHE_PATH.exists():
                self.state.set(tier="Free")
                return

    def _run_server_blocking(self) -> None:
        import server

        os.environ.setdefault("HF_HUB_OFFLINE", "1")  # models are local; never phone home mid-session
        args = [
            "--port", str(self.preferred_port),
            "--whisper-model", str(model_setup.WHISPER_DIR),
            "--nllb-model-dir", str(model_setup.NLLB_DIR),
            "--no-hotspot",
            *server.licensed_launcher_flags(),
            *self.extra_server_args,
        ]
        threading.Thread(target=self._wait_until_serving, daemon=True).start()
        self._server_started = True
        server.main(args)  # blocks for the life of the session
        if not self._quit.is_set():
            raise RuntimeError("The DwaniLive server stopped unexpectedly. " + _last_meaningful_log_line())

    def _wait_until_serving(self) -> None:
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline and self.state.phase == "starting":
            url = self.state.presenter_url
            if url:
                try:
                    local = url.replace("localhost", "127.0.0.1", 1)
                    with urllib.request.urlopen(local, timeout=3) as r:
                        if r.status == 200:
                            self.state.set(phase="ready", auto_open=True)
                            return
                except Exception:
                    pass
            time.sleep(0.5)

    # ---------------------------------------------------------------- window
    def open_window(self) -> None:
        open_app_window(self.ui_url)

    def _watchdog(self) -> None:
        while not self._quit.is_set():
            time.sleep(1)
            st = self.state
            now = time.monotonic()
            if st.closed_at is not None and now - st.closed_at > CLOSE_GRACE_S:
                print("Control window closed -- shutting down.")
                self.shutdown()
            elif now - st.last_seen > HEARTBEAT_TIMEOUT_S:
                print("Control window not responding -- shutting down.")
                self.shutdown()

    def shutdown(self) -> None:
        if self._quit.is_set():
            return
        self._quit.set()
        self._cancel = True
        try:
            import server

            s = getattr(server, "session", None)
            if s is not None:
                s.stop_hotspot()
        except Exception:
            pass
        try:
            appenv.INSTANCE_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        if self._httpd:
            threading.Thread(target=self._httpd.shutdown, daemon=True).start()
        # uvicorn runs in our pipeline thread and owns no signal handlers
        # there; a hard exit is the reliable way to end the process.
        threading.Timer(1.0, lambda: os._exit(0)).start()

    def run(self, open_window: bool = True) -> None:
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        port = self._httpd.server_address[1]
        self.ui_url = f"http://127.0.0.1:{port}/?t={self.token}"
        try:
            appenv.INSTANCE_FILE.write_text(self.ui_url, encoding="utf-8")
        except OSError:
            pass
        print(f"Launcher UI: http://127.0.0.1:{port}/")
        self._start_pipeline()
        threading.Thread(target=self._check_update, name="dwani-update-check", daemon=True).start()
        threading.Thread(target=self._watchdog, daemon=True).start()
        if open_window:
            self.open_window()
        self._httpd.serve_forever()


# ---------------------------------------------------------------------------
# Window helpers
# ---------------------------------------------------------------------------

def _find_browser_for_app_mode() -> Optional[str]:
    if sys.platform != "win32":
        for name in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
            p = shutil.which(name)
            if p:
                return p
        return None
    roots = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    rels = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"]
    for rel in rels:
        for root in roots:
            if root and Path(root, rel).is_file():
                return str(Path(root, rel))
    try:
        import winreg

        for exe in ("msedge.exe", "chrome.exe"):
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                        val, _ = winreg.QueryValueEx(k, None)
                        if val and Path(val).is_file():
                            return val
                except OSError:
                    continue
    except ImportError:
        pass
    return None


def open_app_window(url: str) -> None:
    browser = _find_browser_for_app_mode()
    if browser:
        try:
            subprocess.Popen(
                [browser, f"--app={url}", "--window-size=500,780", "--no-first-run", "--no-default-browser-check"],
                close_fds=True,
            )
            return
        except OSError:
            pass
    webbrowser.open(url)


def _last_meaningful_log_line() -> str:
    _, lines = appenv.LOG.snapshot(10_000)
    for line in reversed(lines):
        s = line.strip()
        if s and not s.startswith(("INFO", "Traceback", "File ", "^")):
            return s
    return "See the log for details."


def diagnostics_text() -> str:
    info = appenv.system_summary()
    info["models"] = {
        "whisper_installed": model_setup.whisper_installed(),
        "nllb_installed": model_setup.nllb_installed(),
        "models_dir": str(appenv.MODELS_DIR),
    }
    try:
        from licensing import DEFAULT_CACHE_PATH

        info["license_token_present"] = DEFAULT_CACHE_PATH.exists()
    except Exception:
        pass
    _, lines = appenv.LOG.snapshot(10_000)
    return (
        "DwaniLive error report\n"
        + json.dumps(info, indent=2)
        + "\n\n--- last log lines ---\n"
        + "\n".join(lines[-150:])
    )
