"""
gui.py

Native window shell for the DwaniLive launcher, replacing the plain console
prompts (progress bar, "License key:" input, etc.) with a window styled to
match the DwaniLive website -- same colour palette and fonts as
logo.html/privacy.html/terms.html ("Rozha One" display font + "Mukta" body
font, the warm cream background, the gradient ध्वनि wordmark).

Built with pywebview (`pip install pywebview`) rather than tkinter: the
brand is already expressed as HTML/CSS across the site, so this window is
just that same HTML/CSS reused, instead of a second, tkinter-shaped
approximation of it. On Windows, pywebview renders through Microsoft Edge
WebView2 (Chromium) -- present by default on Windows 10 21H2+ / Windows 11,
so no extra runtime install for most presenters.

PyInstaller note: pywebview's Windows backend (webview.platforms.winforms)
is imported dynamically from inside pywebview itself, so PyInstaller's
static import analysis misses it unless told -- skip this and the build
silently falls back to opening a normal browser tab instead of a real app
window, with no error printed. Build with:

    pyinstaller --name DwaniLive --onefile --windowed \
        --collect-submodules webview --collect-submodules clr_loader \
        launcher.py

(--windowed, not --console: the console is no longer the UI. Anything the
underlying pipeline still print()s -- setup_models_if_needed(), server.py's
own output, etc. -- gets captured by the stdout tee below and shown inside
the window's "technical log" panel instead of a terminal.)

Design: this module owns ALL view transitions (view-splash -> view-download
-> view-activate -> view-starting -> view-ready, or -> view-error at any
point). launcher.py's main() calls run(...) with the actual pipeline
pieces (setup_models_if_needed, setup_firewall_if_needed, the constants) --
gui.py doesn't import launcher.py itself, to avoid a circular import.

Two things this module deliberately does NOT try to parse or guess:
  - The exact join-URL/QR format session.py produces. session.py wasn't
    available while writing this, so rather than fabricate its shape,
    whatever it actually prints is shown verbatim in the "ready" view's
    log panel (same information the console gave before, same wording,
    just inside the window instead of a terminal).
  - Anything about server.py's internals beyond what its own module
    docstring/prints already confirm -- e.g. the "ready" transition is
    triggered by literally seeing server.py's own
    `print(f"Presenter mic page: {host_url}")` line go by, not by calling
    into server.py's internals directly.
"""

from __future__ import annotations

import re
import sys
import threading
import traceback
import webbrowser
from pathlib import Path
from typing import Callable, Optional

import webview

# ---------------------------------------------------------------------------
# Brand styling -- lifted directly from the website's :root variables (see
# logo.html / privacy.html / terms.html) so this window matches instead of
# inventing a second, different-looking palette.
# ---------------------------------------------------------------------------

_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>DwaniLive</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Rozha+One&family=Mukta:wght@400;500;600;700;800&display=swap" rel="stylesheet">
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
</style>
</head>
<body>

<span class="brand-name"><span class="dwani" lang="hi">ध्वनि</span><span class="live">Live</span></span>

<div class="card">

  <section class="view" id="view-splash">
    <div class="status-line">Starting…</div>
  </section>

  <section class="view" id="view-download">
    <h1>Setting up DwaniLive</h1>
    <p class="subtitle">Downloading translation models (one-time, ~few hundred MB). This won't happen again on this computer.</p>
    <div class="progress-track"><div class="progress-fill" id="progress-fill"></div></div>
    <div class="progress-stats"><span id="progress-pct">0%</span><span id="progress-mb">0.0 / 0.0 MB</span></div>
  </section>

  <section class="view" id="view-activate">
    <h1>Activate DwaniLive</h1>
    <p class="subtitle">Enter your license details once, from your purchase confirmation. After this, it works fully offline.</p>
    <form id="activate-form">
      <label for="license-key">License key</label>
      <input type="text" id="license-key" autocomplete="off" required>
      <label for="license-email">Email (used at checkout)</label>
      <input type="email" id="license-email" autocomplete="off" required>
      <div class="error-banner" id="activate-error"></div>
      <button type="submit" class="primary" id="activate-btn">Activate</button>
    </form>
    <div class="fine-print">This is a one-time step. Needs internet now; the session itself runs fully offline.</div>
  </section>

  <section class="view" id="view-starting">
    <h1>Starting DwaniLive…</h1>
    <div class="spinner-row"><div class="spinner"></div><div class="status-line" id="starting-status">Getting things ready…</div></div>
    <button class="log-toggle" id="starting-log-toggle">Show details</button>
    <div class="log-panel" id="starting-log"></div>
  </section>

  <section class="view" id="view-ready">
    <div class="ok-badge">&#10003;</div>
    <h1>DwaniLive is running</h1>
    <p class="subtitle">Keep this window open for the length of the session.</p>
    <div class="section-label">Presenter</div>
    <a href="#" class="link-button" id="open-presenter-btn">Open presenter mic page ↗</a>
    <div class="section-label">Attendees</div>
    <p class="subtitle" style="margin-bottom:0.4rem;">Share the link and QR code below (same as before — just shown here instead of a terminal window):</p>
    <div class="log-panel show" id="ready-log" style="flex:1;"></div>
  </section>

  <section class="view" id="view-error">
    <div class="error-icon">&#9888;</div>
    <h1>Something went wrong</h1>
    <p class="subtitle" id="error-message">Unknown error.</p>
    <button class="secondary" id="quit-btn">Quit</button>
    <button class="log-toggle" id="error-log-toggle">Show details</button>
    <div class="log-panel" id="error-log"></div>
  </section>

</div>

<script>
  function showView(name) {
    document.querySelectorAll('.view').forEach(function (el) {
      el.classList.toggle('active', el.id === 'view-' + name);
    });
  }

  function appendLog(elId, line) {
    var el = document.getElementById(elId);
    el.textContent += (el.textContent ? "\n" : "") + line;
    el.scrollTop = el.scrollHeight;
  }

  function setProgress(pct, mbDone, mbTotal) {
    document.getElementById('progress-fill').style.width = pct + '%';
    document.getElementById('progress-pct').textContent = pct + '%';
    document.getElementById('progress-mb').textContent = mbDone.toFixed(1) + ' / ' + mbTotal.toFixed(1) + ' MB';
  }

  function setStartingStatus(text) {
    document.getElementById('starting-status').textContent = text;
  }

  function showReady(presenterUrl) {
    var btn = document.getElementById('open-presenter-btn');
    btn.onclick = function (e) {
      e.preventDefault();
      window.pywebview.api.open_url(presenterUrl);
    };
    showView('ready');
  }

  function showError(message) {
    document.getElementById('error-message').textContent = message;
    showView('error');
  }

  document.getElementById('starting-log-toggle').addEventListener('click', function () {
    var panel = document.getElementById('starting-log');
    panel.classList.toggle('show');
    this.textContent = panel.classList.contains('show') ? 'Hide details' : 'Show details';
  });
  document.getElementById('error-log-toggle').addEventListener('click', function () {
    var panel = document.getElementById('error-log');
    panel.classList.toggle('show');
    this.textContent = panel.classList.contains('show') ? 'Hide details' : 'Show details';
  });
  document.getElementById('quit-btn').addEventListener('click', function () {
    window.pywebview.api.quit();
  });

  document.getElementById('activate-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var key = document.getElementById('license-key').value.trim();
    var email = document.getElementById('license-email').value.trim();
    var btn = document.getElementById('activate-btn');
    var errBox = document.getElementById('activate-error');
    errBox.classList.remove('show');
    btn.disabled = true;
    btn.textContent = 'Activating…';
    window.pywebview.api.activate(key, email).then(function (result) {
      if (result.ok) {
        showView('starting');
      } else {
        btn.disabled = false;
        btn.textContent = 'Activate';
        errBox.textContent = result.error || 'Activation failed. Check your details and try again.';
        errBox.classList.add('show');
      }
    });
  });

  showView('splash');
</script>
</body>
</html>
"""

_PRESENTER_URL_RE = re.compile(r"Presenter mic page:\s*(\S+)")


class _TeeStdout:
    """Mirrors everything written to a real stream (stdout/stderr) to an
    additional callback, line-by-line, without changing what the real
    stream sees. Used so every print() already scattered through
    setup_models_if_needed(), setup_firewall_if_needed(), and server.py
    itself shows up live in the window's log panel -- rather than needing
    every one of those call sites rewritten to know about the GUI."""

    def __init__(self, real_stream, on_line: Callable[[str], None]):
        self._real = real_stream
        self._on_line = on_line
        self._buf = ""

    def write(self, s: str) -> int:
        self._real.write(s)
        self._buf += s
        while "\n" in self._buf or "\r" in self._buf:
            # server.py's own progress bars use \r; treat it like \n here
            # so the log panel gets a fresh line per update instead of one
            # giant run-on string.
            idx_n = self._buf.find("\n")
            idx_r = self._buf.find("\r")
            idx = min(x for x in (idx_n, idx_r) if x != -1)
            line, self._buf = self._buf[:idx], self._buf[idx + 1:]
            if line.strip():
                try:
                    self._on_line(line)
                except Exception:
                    pass  # a log-panel hiccup should never take down setup/the server
        return len(s)

    def flush(self) -> None:
        self._real.flush()

    def isatty(self) -> bool:
        return False


class _CaptureAndForward:
    """Like _TeeStdout, but also keeps its own copy of the lines it saw --
    used only around the activate() call, to pull its actual printed error
    detail (bad key/email, subscription not active, cold-server timeout,
    etc. -- see activate.py) into the inline error banner, instead of that
    detail only being visible in the (hidden, at that point) log panel."""

    def __init__(self, forward_to):
        self._forward = forward_to
        self._buf = ""
        self.lines: list[str] = []

    def write(self, s: str) -> int:
        self._forward.write(s)
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self.lines.append(line)
        return len(s)

    def flush(self) -> None:
        self._forward.flush()

    def isatty(self) -> bool:
        return False


class Api:
    """Methods callable from the page's JS via window.pywebview.api.*.
    Kept intentionally tiny -- everything else (view transitions, progress,
    log lines) is pushed from Python to JS via evaluate_js instead, so
    there's one place (this module) driving the whole flow.
    """

    def __init__(self, gui: "LauncherGUI"):
        self._gui = gui

    def activate(self, license_key: str, email: str) -> dict:
        from activate import activate as do_activate

        # activate() only returns True/False -- its actually-useful error
        # detail (bad key/email, subscription not active yet, server cold
        # and timed out, etc.) goes to stderr via print(). Capture that
        # here so it reaches the inline error banner instead of only the
        # (not yet visible, at this point) log panel.
        capture = _CaptureAndForward(sys.stderr)
        old_stderr = sys.stderr
        sys.stderr = capture
        try:
            ok = do_activate(license_key, self._gui.license_server_url, email)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            sys.stderr = old_stderr

        if not ok:
            detail = "\n".join(capture.lines) if capture.lines else \
                "Activation failed. Check your license key and email and try again."
            return {"ok": False, "error": detail}

        # Success: hand off to the same continuation the "already cached"
        # path uses, in a background thread so this JS call returns
        # immediately instead of blocking on model loading/server startup.
        threading.Thread(target=self._gui._start_server_phase, daemon=True).start()
        return {"ok": True}

    def open_url(self, url: str) -> None:
        webbrowser.open(url)

    def quit(self) -> None:
        for w in webview.windows:
            w.destroy()


class LauncherGUI:
    def __init__(
        self,
        *,
        setup_models_if_needed: Callable[..., None],
        setup_firewall_if_needed: Callable[[], None],
        app_dir: Path,
        nllb_model_dir: Path,
        whisper_model_size: str,
        server_port: int,
        license_server_url: str,
    ):
        self._setup_models_if_needed = setup_models_if_needed
        self._setup_firewall_if_needed = setup_firewall_if_needed
        self.app_dir = app_dir
        self.nllb_model_dir = nllb_model_dir
        self.whisper_model_size = whisper_model_size
        self.server_port = server_port
        self.license_server_url = license_server_url

        self._window: Optional[webview.Window] = None
        self._ready_shown = False

    # -- small evaluate_js wrappers, all guarded so a display hiccup never
    # takes down setup or the running server -----------------------------
    def _js(self, code: str) -> None:
        if self._window is None:
            return
        try:
            self._window.evaluate_js(code)
        except Exception:
            pass

    @staticmethod
    def _js_str(s: str) -> str:
        return (
            s.replace("\\", "\\\\").replace("`", "\\`").replace("</script>", "<\\/script>")
        )

    def _show_view(self, name: str) -> None:
        self._js(f"showView('{name}')")

    def _set_progress(self, pct: int, mb_done: float, mb_total: float) -> None:
        self._js(f"setProgress({pct}, {mb_done}, {mb_total})")

    def _set_status(self, text: str) -> None:
        self._js(f"setStartingStatus(`{self._js_str(text)}`)")

    def _append_log(self, line: str) -> None:
        target = "ready-log" if self._ready_shown else "starting-log"
        self._js(f"appendLog('{target}', `{self._js_str(line)}`)")

    def _show_ready(self, presenter_url: str) -> None:
        self._ready_shown = True
        self._js(f"showReady(`{self._js_str(presenter_url)}`)")

    def _show_error(self, message: str) -> None:
        self._js(f"showError(`{self._js_str(message)}`)")

    # -- the actual pipeline ------------------------------------------------
    def _on_log_line(self, line: str) -> None:
        self._append_log(line)
        if not self._ready_shown:
            match = _PRESENTER_URL_RE.search(line)
            if match:
                self._show_ready(match.group(1))

    def _run_pipeline(self, window: webview.Window) -> None:
        self._window = window
        # webview.start()'s func runs as soon as the GUI loop starts, which
        # can be before the page has actually finished loading -- without
        # this wait, a fast path (models already cached, license already
        # activated) could call evaluate_js() before there's any JS to
        # evaluate against. Bounded timeout so a slow/failed page load
        # degrades to "keep going anyway" rather than hanging setup forever.
        window.events.loaded.wait(10)

        tee_out = _TeeStdout(sys.stdout, self._on_log_line)
        tee_err = _TeeStdout(sys.stderr, self._on_log_line)
        sys.stdout, sys.stderr = tee_out, tee_err

        try:
            from licensing import DEFAULT_CACHE_PATH

            self._show_view("download")
            self._setup_models_if_needed(
                progress_cb=lambda pct, mb_done, mb_total: self._set_progress(pct, mb_done, mb_total),
                interactive_on_error=False,
            )

            if DEFAULT_CACHE_PATH.exists():
                self._start_server_phase()
            else:
                self._show_view("activate")
                # Api.activate() takes it from here once the presenter submits
                # the form -- see Api.activate() above.
        except Exception as exc:
            traceback.print_exc()
            self._show_error(str(exc))

    def _start_server_phase(self) -> None:
        try:
            self._show_view("starting")
            self._set_status("Setting up local network access…")
            self._setup_firewall_if_needed()

            self._set_status(
                "Loading translation models and starting the server (this can take a "
                "minute the first time a model loads)…"
            )

            import server  # deliberately imported here, same reasoning as launcher.py's console path

            # Blocking call (uvicorn.run) -- this method already runs on a
            # background thread (see Api.activate() / _run_pipeline()), so
            # blocking here is fine; it's what keeps the server alive.
            server.main([
                "--port", str(self.server_port),
                "--whisper-model", self.whisper_model_size,
                "--nllb-model-dir", str(self.nllb_model_dir),
                "--qa",
            ])
        except Exception as exc:
            traceback.print_exc()
            self._show_error(str(exc))


def run(
    *,
    setup_models_if_needed: Callable[..., None],
    setup_firewall_if_needed: Callable[[], None],
    app_dir: Path,
    nllb_model_dir: Path,
    whisper_model_size: str,
    server_port: int,
    license_server_url: str,
) -> None:
    """Entry point called from launcher.py's main(). Blocks until the
    window is closed (webview.start() blocks the calling thread, same as
    uvicorn.run() did in the console path)."""
    gui = LauncherGUI(
        setup_models_if_needed=setup_models_if_needed,
        setup_firewall_if_needed=setup_firewall_if_needed,
        app_dir=app_dir,
        nllb_model_dir=nllb_model_dir,
        whisper_model_size=whisper_model_size,
        server_port=server_port,
        license_server_url=license_server_url,
    )
    window = webview.create_window(
        "DwaniLive",
        html=_HTML,
        js_api=Api(gui),
        width=460,
        height=680,
        min_size=(400, 560),
        background_color="#fdf6e7",
        confirm_close=True,
    )
    webview.start(gui._run_pipeline, window)
