from __future__ import annotations

import html
import json
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


APP_NAME = "Decades Tracker"
HOST = "127.0.0.1"
PORT = 9876
URL = f"http://{HOST}:{PORT}"
RELAY_SCRIPT = "SeveralUDOClockRelay.ps1"


def resource(relative: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / relative


def data_root() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "DecadesTracker"
    root.mkdir(parents=True, exist_ok=True)
    return root


def port_open() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=.4):
            return True
    except OSError:
        return False


def tracker_ready() -> bool:
    try:
        with urllib.request.urlopen(URL + "/healthz", timeout=.6) as response:
            return response.status == 200
    except Exception:
        return False


def write_startup_error(details: str) -> Path:
    destination = data_root() / "startup-error.log"
    destination.write_text(details, encoding="utf-8")
    return destination


def clock_sync_folder() -> Path | None:
    """Locate the user's installed Clock Sync folder without changing it."""
    override = str(os.environ.get("SEVERALUDO_CLOCK_SYNC_DIR") or "").strip()
    roots = [Path(override)] if override else []
    for variable in ("USERPROFILE", "OneDrive", "OneDriveConsumer"):
        value = str(os.environ.get(variable) or "").strip()
        if value:
            roots.append(Path(value) / "Documents")
    roots.append(Path.home() / "Documents")
    seen: set[str] = set()
    for root in roots:
        folder = root if root.name == "SeveralUDOClockSync" else root / "Electronic Arts" / "The Sims 4" / "Mods" / "SeveralUDOClockSync"
        key = str(folder).casefold()
        if key in seen:
            continue
        seen.add(key)
        if (folder / RELAY_SCRIPT).is_file():
            return folder
    return None


def relay_heartbeat_fresh(folder: Path, maximum_age: float = 12.0) -> bool:
    """Use the relay's heartbeat to recognize an already-running instance."""
    try:
        payload = json.loads((folder / "relay_health.json").read_text(encoding="utf-8-sig"))
        checked = str(payload.get("checked_at") or "").replace("Z", "+00:00")
        moment = datetime.fromisoformat(checked)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - moment.astimezone(timezone.utc)).total_seconds()) <= maximum_age
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


class RelaySupervisor:
    """Keep the installed relay alive while the native tracker is open."""

    def __init__(self, check_seconds: float = 3.0) -> None:
        self.check_seconds = check_seconds
        self.process: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.launched_at = 0.0

    def _launch(self, folder: Path) -> None:
        system_root = Path(os.environ.get("SystemRoot") or r"C:\Windows")
        powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        executable = str(powershell if powershell.is_file() else "powershell.exe")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(folder / RELAY_SCRIPT)],
            cwd=str(folder), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        self.launched_at = time.monotonic()

    def _stop_owned_process(self) -> None:
        if self.process is None or self.process.poll() is not None:
            self.process = None
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.process = None

    def ensure_running(self) -> None:
        folder = clock_sync_folder()
        if folder is None:
            return
        fresh = relay_heartbeat_fresh(folder)
        if self.process is not None and self.process.poll() is None:
            if fresh or time.monotonic() - self.launched_at <= 15:
                return
            self._stop_owned_process()
        else:
            self.process = None
        if not fresh:
            self._launch(folder)

    def _monitor(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.ensure_running()
            except OSError:
                pass
            self.stop_event.wait(self.check_seconds)

    def start(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._monitor, name="DecadesTrackerRelay", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self._stop_owned_process()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=5)


class LocalTracker:
    """Own the local API server only when this process started it."""

    def __init__(self) -> None:
        self.server = None
        self.thread: threading.Thread | None = None
        self.owned = False
        self.error = ""

    def start(self) -> None:
        if tracker_ready():
            return
        self.owned = True

        def run_server() -> None:
            try:
                import uvicorn
                from app.main import app

                self.server = uvicorn.Server(uvicorn.Config(
                    app,
                    host=HOST,
                    port=PORT,
                    log_config=None,
                    access_log=False,
                ))
                self.server.run()
            except Exception:
                self.error = traceback.format_exc()
                write_startup_error(self.error)

        self.thread = threading.Thread(
            target=run_server,
            name="DecadesTrackerServer",
            daemon=True,
        )
        self.thread.start()

    def wait_until_ready(self, timeout: float = 45.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if tracker_ready():
                return True
            if self.error or (self.thread is not None and not self.thread.is_alive()):
                return False
            time.sleep(.15)
        return False

    def stop(self) -> None:
        if not self.owned:
            return
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=8)


def startup_failure_html(log_path: Path) -> str:
    safe_path = html.escape(str(log_path))
    return f"""<!doctype html><html><head><meta charset=\"utf-8\"><style>
html,body{{height:100%;margin:0;background:#101217;color:#eee8dc;font-family:'Segoe UI',sans-serif}}
body{{display:grid;place-items:center}}main{{max-width:620px;padding:42px;border:1px solid #9a7135;background:#17191e}}
h1{{font:700 32px Georgia,serif}}p{{line-height:1.6;color:#c9c3b8}}code{{word-break:break-all;color:#d8a44e}}
</style></head><body><main><h1>The chronicle could not open</h1>
<p>The local tracker server did not become ready. No save data was removed.</p>
<p>Diagnostic details were written to:<br><code>{safe_path}</code></p>
</main></body></html>"""


def open_native_window(tracker: LocalTracker) -> None:
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    loading_page = resource("assets/loading.html")
    icon = resource("assets/decades-app-icon.ico")
    storage = data_root() / "webview-profile"
    storage.mkdir(parents=True, exist_ok=True)
    window = webview.create_window(
        APP_NAME,
        url=str(loading_page),
        width=1380,
        height=900,
        min_size=(900, 640),
        maximized=True,
        background_color="#101217",
        text_select=True,
        zoomable=True,
    )

    def load_tracker(target) -> None:
        if tracker.wait_until_ready():
            target.load_url(URL)
            return
        log_path = write_startup_error(tracker.error or "The local tracker timed out during startup.\n")
        target.load_html(startup_failure_html(log_path))

    webview.start(
        load_tracker,
        args=(window,),
        gui="edgechromium",
        private_mode=False,
        storage_path=str(storage),
        icon=str(icon),
    )


def native_error_dialog(message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
    except Exception:
        pass


def main() -> None:
    root = data_root()
    db_path = root / "decades-v4.db"
    os.environ.setdefault("DATABASE_URL", "sqlite:///" + db_path.as_posix())
    os.environ.setdefault("PUBLIC_URL", URL)
    tracker = LocalTracker()
    relay = RelaySupervisor()
    tracker.start()
    relay.start()
    try:
        open_native_window(tracker)
    finally:
        relay.stop()
        tracker.stop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_path = write_startup_error(traceback.format_exc())
        native_error_dialog(
            "Decades Tracker could not open its Windows window. "
            f"Diagnostic details were saved to:\n{log_path}"
        )
