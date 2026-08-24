from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
import traceback
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


def open_app() -> None:
    edge = Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    if not edge.exists():
        edge = Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    if edge.exists():
        import subprocess
        subprocess.Popen([str(edge), f"--app={URL}", "--start-maximized"])
    else:
        webbrowser.open(URL)


def show_splash(start_server) -> None:
    try:
        import tkinter as tk
        window = tk.Tk(); window.overrideredirect(True); window.configure(bg="#111318")
        width, height = 520, 310
        x = (window.winfo_screenwidth() - width) // 2; y = (window.winfo_screenheight() - height) // 2
        window.geometry(f"{width}x{height}+{x}+{y}")
        frame = tk.Frame(window, bg="#17191e", highlightbackground="#9a7135", highlightthickness=2); frame.pack(fill="both", expand=True, padx=8, pady=8)
        try:
            logo = tk.PhotoImage(file=str(resource("assets/decades-app-icon.png"))).subsample(5, 5)
            label = tk.Label(frame, image=logo, bg="#17191e"); label.image = logo; label.pack(pady=(20, 5))
        except Exception:
            pass
        tk.Label(frame, text="DECΛDES", fg="#eee8dc", bg="#17191e", font=("Georgia", 30, "bold")).pack()
        tk.Label(frame, text="THE LIVING FAMILY CHRONICLE", fg="#c79442", bg="#17191e", font=("Segoe UI", 9, "bold")).pack(pady=(0, 20))
        status = tk.Label(frame, text="Opening your chronicle…", fg="#aaa59b", bg="#17191e", font=("Segoe UI", 10)); status.pack()
        start_server()
        def check():
            try:
                urllib.request.urlopen(URL + "/healthz", timeout=.5).read()
                status.configure(text="Chronicle ready")
                window.after(350, lambda: (open_app(), window.destroy()))
            except Exception:
                window.after(150, check)
        check(); window.mainloop()
    except Exception:
        start_server()
        for _ in range(100):
            if port_open(): break
            time.sleep(.1)
        open_app()


def show_native_splash(start_server) -> None:
    """Use a dedicated Edge app window as a lightweight, console-free splash."""
    edge = Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    if not edge.exists():
        edge = Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    splash = None
    if edge.exists():
        import subprocess
        splash_url = resource("assets/loading.html").as_uri()
        profile = data_root() / "splash-profile"
        splash = subprocess.Popen([str(edge), f"--app={splash_url}", f"--user-data-dir={profile}", "--window-size=540,350", "--no-first-run"])
    start_server()
    for _ in range(200):
        try:
            urllib.request.urlopen(URL + "/healthz", timeout=.4).read(); break
        except Exception:
            time.sleep(.1)
    if splash and splash.poll() is None:
        splash.terminate()
    open_app()


def main() -> None:
    if port_open():
        open_app(); return
    db_path = data_root() / "decades-v4.db"
    os.environ.setdefault("DATABASE_URL", "sqlite:///" + db_path.as_posix())
    os.environ.setdefault("PUBLIC_URL", URL)
    holder = {}
    def start_server():
        if holder: return
        def run_server():
            try:
                import uvicorn
                from app.main import app
                server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_config=None, access_log=False))
                holder["server"] = server; server.run()
            except Exception:
                (data_root() / "startup-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        holder["starting"] = True
        thread = threading.Thread(target=run_server, name="DecadesTrackerServer", daemon=False)
        holder["thread"] = thread; thread.start()
    show_native_splash(start_server)
    if holder.get("thread"):
        holder["thread"].join()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        try:
            (data_root() / "startup-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        except Exception:
            pass
