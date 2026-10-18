"""Pre-run checks: Python, dependencies, files, dataset integrity, port, write access.
Usage: python scripts/preflight.py [--port 8000] [--skip-port]. Exit code 1 if any hard check fails."""
import argparse, hashlib, importlib, json, os, socket, sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPS = [("fastapi", "fastapi"), ("uvicorn", "uvicorn"), ("pymupdf", "pymupdf"), ("python-multipart", "multipart"),
        ("reportlab", "reportlab"), ("pytest", "pytest"), ("httpx", "httpx")]
fails, warns = [], []

def ok(msg): print(f"  [ OK ] {msg}")
def fail(msg): fails.append(msg); print(f"  [FAIL] {msg}")
def warn(msg): warns.append(msg); print(f"  [WARN] {msg}")

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000"))); ap.add_argument("--skip-port", action="store_true")
    a = ap.parse_args()
    print("BankEase AI preflight")
    v = sys.version_info
    (ok if v >= (3, 10) else fail)(f"Python {v.major}.{v.minor}.{v.micro}" + ("" if v >= (3, 10) else " (need 3.10+)"))
    for pkg, mod in DEPS:
        try:
            importlib.import_module(mod); ok(f"{pkg} {metadata.version(pkg)}")
        except Exception as e:
            fail(f"{pkg} not importable ({type(e).__name__}) -> run: make install")
    try:
        sys.path.insert(0, str(ROOT)); importlib.import_module("backend.app.main"); ok("backend app imports cleanly")
    except Exception as e:
        fail(f"backend app failed to import: {e}")
    (ok if (ROOT / "backend/static/index.html").exists() else fail)("frontend backend/static/index.html present")
    man = ROOT / "data/samples/manifest.json"
    if not man.exists(): warn("sample dataset missing -> run: make data")
    else:
        files = json.loads(man.read_text())["files"]
        bad = [f["file"] for f in files if not (ROOT / "data/samples" / f["file"]).exists() or hashlib.sha256((ROOT / "data/samples" / f["file"]).read_bytes()).hexdigest() != f["sha256"]]
        (ok if not bad else warn)(f"dataset: {len(files)} files verified" if not bad else f"dataset files missing/changed: {bad[:3]}... -> run: make data")
    rt = Path(os.getenv("BANKEASE_DATA", ROOT / "data/runtime"))
    try:
        rt.mkdir(parents=True, exist_ok=True); t = rt / ".w"; t.write_text("x"); t.unlink(); ok(f"storage dir writable: {rt}")
    except Exception as e:
        fail(f"storage dir not writable ({rt}): {e}")
    if not a.skip_port:
        with socket.socket() as s:
            s.settimeout(0.5)
            (fail if s.connect_ex(("127.0.0.1", a.port)) == 0 else ok)(f"port {a.port} " + ("is already in use (set PORT=xxxx)" if s.connect_ex(("127.0.0.1", a.port)) == 0 else "is free"))
    print(f"Result: {'FAILED (' + str(len(fails)) + ' problem(s))' if fails else 'all required checks passed'}" + (f", {len(warns)} warning(s)" if warns else ""))
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()
