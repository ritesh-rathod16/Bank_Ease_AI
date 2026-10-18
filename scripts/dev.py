"""One command for everything: preflight -> start the backend, which also serves the frontend (same port).
Usage: python scripts/dev.py [--port 8000] [--no-reload] [--open]"""
import argparse, os, subprocess, sys, threading, time, webbrowser
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    ap.add_argument("--no-reload", action="store_true"); ap.add_argument("--open", action="store_true"); a = ap.parse_args()
    if subprocess.call([sys.executable, str(ROOT / "scripts/preflight.py"), "--port", str(a.port)]) != 0:
        sys.exit("\nPreflight failed - fix the items above (usually: make install) and retry.")
    url = f"http://localhost:{a.port}"
    print(f"\nBackend API : {url}/docs\nFrontend UI : {url}/\nHealth      : {url}/health\nCtrl+C to stop.\n")
    if a.open: threading.Thread(target=lambda: (time.sleep(2), webbrowser.open(url)), daemon=True).start()
    cmd = [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--port", str(a.port), "--host", "127.0.0.1"]
    if not a.no_reload: cmd += ["--reload", "--reload-dir", "backend"]
    try: sys.exit(subprocess.call(cmd, cwd=ROOT))
    except KeyboardInterrupt: pass

if __name__ == "__main__":
    main()
