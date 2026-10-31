"""Start the FastAPI API for local development; the Next.js UI runs separately via npm run dev:all.
Usage: python scripts/dev.py [--port 8000] [--no-reload] [--open]"""
import argparse, json, os, socket, subprocess, sys, threading, time, webbrowser
from pathlib import Path
from urllib.request import urlopen
ROOT = Path(__file__).resolve().parents[1]

def load_local_env():
    """Load simple KEY=VALUE development settings without printing secrets."""
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not separator or not key or not key.replace("_", "").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)

def port_is_free(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False

def bankease_api_is_running(url):
    try:
        with urlopen(f"{url}/openapi.json", timeout=1) as response:
            spec = json.load(response)
        if spec.get("info", {}).get("title") != "BankEase AI":
            return False
        with urlopen(f"{url}/health", timeout=1) as response:
            health = json.load(response)
        return health.get("status") == "ok"
    except (OSError, ValueError, AttributeError, TypeError):
        return False

def main():
    load_local_env()
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    ap.add_argument("--no-reload", action="store_true"); ap.add_argument("--open", action="store_true"); a = ap.parse_args()
    if subprocess.call([sys.executable, str(ROOT / "scripts/preflight.py"), "--port", str(a.port), "--skip-port"]) != 0:
        sys.exit("\nPreflight failed - fix the items above (usually: make install) and retry.")
    url = f"http://localhost:{a.port}"
    if not port_is_free(a.port):
        if bankease_api_is_running(url):
            print(f"\nBackend API : {url}/docs (reusing existing BankEase API)\nFrontend UI : http://localhost:3000 (run npm run dev:all for both)\nHealth      : {url}/health\nCtrl+C to stop the frontend. The existing API will remain running.\n")
            if a.open: threading.Thread(target=lambda: (time.sleep(2), webbrowser.open(url)), daemon=True).start()
            return
        sys.exit(f"\nPort {a.port} is occupied by another service. Stop it or set PORT to a free port and update BANKEASE_API_ORIGIN for the frontend.")
    print(f"\nBackend API : {url}/docs\nFrontend UI : http://localhost:3000 (run npm run dev:all for both)\nHealth      : {url}/health\nCtrl+C to stop.\n")
    if a.open: threading.Thread(target=lambda: (time.sleep(2), webbrowser.open(url)), daemon=True).start()
    cmd = [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--port", str(a.port), "--host", "127.0.0.1"]
    if not a.no_reload: cmd += ["--reload", "--reload-dir", "backend"]
    try: sys.exit(subprocess.call(cmd, cwd=ROOT))
    except KeyboardInterrupt: pass

if __name__ == "__main__":
    main()
