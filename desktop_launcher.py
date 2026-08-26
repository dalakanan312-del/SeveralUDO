from __future__ import annotations

import html
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path


APP_NAME = "Decades Tracker"
HOST = "127.0.0.1"
PORT = 9876
URL = f"http://{HOST}:{PORT}"


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
    tracker.start()
    try:
        open_native_window(tracker)
    finally:
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
