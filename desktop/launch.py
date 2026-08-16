"""Methane Atlas as a desktop app.

Opens the map in a Chrome app window — no tabs, no address bar — the same way
Gridline and TryLine do. The whole site is baked into the executable, so this
needs nothing on the machine except a browser: not the repository, not Node,
not a network connection. The map, 798 plumes, the imagery and the facility
records all work offline.

WHY A SERVER AT ALL
-------------------
The page could be opened straight off disk, but file:// gives it a null origin
and the app fetches its data as JSON. Those fetches fail under file://. A small
loopback server avoids the whole class of problem.

WHY IT LOGS
-----------
Frozen with console=False, every failure is invisible: no Chrome, port already
taken, payload not extracted — all of them look like a double-click that did
nothing. Every run appends to %LOCALAPPDATA%\\MethaneAtlas\\launch.log, so
"nothing happened" always has an answer.

WHY THE HEALTH TOKEN
--------------------
TryLine is on 5017 and Gridline on 5018/5019, so this takes 5020. The port
alone is not enough: if something else is already answering there, a bare
"is the socket up" check would attach to it and serve the wrong page. The
server identifies itself and the launcher insists on hearing its own name back.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP = "Methane Atlas"
DEFAULT_PORT = 5020
HEALTH_PATH = "/__matlas_health"
HEALTH_TOKEN = "methane-atlas-ok"

# Keep serving briefly after the window closes so a reload still works.
LINGER_SECONDS = 20
# How long to wait for a cold Chrome start before giving up on the window.
STARTUP_SECONDS = 25

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

STATE = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))) / "MethaneAtlas"
LOG_PATH = STATE / "launch.log"


def log(message: str) -> None:
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}\n")
    except OSError:
        pass  # a launcher must never die because it could not write its log


def site_root() -> Path:
    """Where the built site lives — inside the exe when frozen, in the repo otherwise."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ".")) / "site"
    return Path(__file__).resolve().parents[1] / "web" / "out"


class Handler(SimpleHTTPRequestHandler):
    """Static files, plus one endpoint that proves which app is answering."""

    def do_GET(self):  # noqa: N802 - name fixed by the base class
        if self.path.split("?")[0] == HEALTH_PATH:
            body = HEALTH_TOKEN.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *args):  # keep the console quiet; we have a log file
        pass


def ours(port: int) -> bool:
    """True only when the thing on this port is this app."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{HEALTH_PATH}", timeout=1) as r:
            return r.read().decode().strip() == HEALTH_TOKEN
    except Exception:
        return False


def port_free(port: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick_port() -> tuple[int, bool]:
    """(port, already_running). Reuses our own server; steps past anyone else's."""
    for port in range(DEFAULT_PORT, DEFAULT_PORT + 12):
        if ours(port):
            return port, True
        if port_free(port):
            return port, False
    return DEFAULT_PORT, False


def open_window(url: str) -> subprocess.Popen | None:
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    browser = next((c for c in CHROME_CANDIDATES if os.path.exists(c)), None)
    if not browser:
        log("no Chrome or Edge found; falling back to the default browser")
        import webbrowser

        webbrowser.open(url)
        return None
    # A dedicated profile keeps the app window out of the user's normal
    # session, so it cannot inherit or disturb their tabs.
    profile = STATE / "browser"
    log(f"opening {Path(browser).name} at {url}")
    return subprocess.Popen(
        [
            browser,
            f"--app={url}",
            f"--user-data-dir={profile}",
            "--window-size=1500,950",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        creationflags=no_window,
    )


def main() -> int:
    log(f"--- {APP} launching ---")
    root = site_root()
    index = root / "index.html"
    if not index.exists():
        log(f"FATAL: site not found at {root}")
        log("If running from source, build it first:  cd web && npm run build")
        return 1
    log(f"serving {root}")

    port, already = pick_port()
    url = f"http://127.0.0.1:{port}/"

    if already:
        # Another copy is up. Just raise a window against it and leave its
        # server alone — two servers on one port is the bug this avoids.
        log(f"already running on {port}; opening another window")
        open_window(url)
        return 0

    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"server up on {port}")

    deadline = time.time() + STARTUP_SECONDS
    while not ours(port) and time.time() < deadline:
        time.sleep(0.2)
    if not ours(port):
        log("FATAL: server did not answer its own health check")
        return 1

    browser = open_window(url)
    if browser is None:
        time.sleep(LINGER_SECONDS)
        return 0

    try:
        browser.wait()
    except KeyboardInterrupt:
        pass
    # Chrome often hands a new window to an already-running browser and exits
    # immediately, so its exit is not proof the window closed. Linger either
    # way; a short wait costs nothing and a premature shutdown breaks reload.
    log("browser process exited; lingering briefly")
    time.sleep(LINGER_SECONDS)
    server.shutdown()
    log("stopped")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        log("UNHANDLED:\n" + traceback.format_exc())
        raise
