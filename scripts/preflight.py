"""Pre-run checks: Python, dependencies, files, dataset integrity, port, write access.
Usage: python scripts/preflight.py [--port 8000] [--skip-port]. Exit code 1 if any hard check fails."""
import argparse, hashlib, importlib, json, os, socket, sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPS = [("fastapi", "fastapi"), ("uvicorn", "uvicorn"), ("pymupdf", "pymupdf"), ("python-multipart", "multipart"),
        ("reportlab", "reportlab"), ("pymongo", "pymongo"), ("dnspython", "dns"),
        ("argon2-cffi", "argon2"), ("pytest", "pytest"), ("httpx", "httpx")]
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
    required_frontend = [ROOT / "frontend/src/app/page.tsx", ROOT / "frontend/src/app/layout.tsx", ROOT / "frontend/next.config.ts", ROOT / "package.json"]
    missing_frontend = [str(path.relative_to(ROOT)) for path in required_frontend if not path.exists()]
    (ok if not missing_frontend else fail)("Next.js App Router frontend present" if not missing_frontend else f"frontend files missing: {missing_frontend}")
    if not (ROOT / "node_modules/next/package.json").exists():
        warn("Next.js dependencies are not installed -> run: npm install")
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
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                s.bind(("127.0.0.1", a.port))
            ok(f"port {a.port} is free")
        except OSError:
            fail(f"port {a.port} is already in use (set PORT=xxxx)")
    print(f"Result: {'FAILED (' + str(len(fails)) + ' problem(s))' if fails else 'all required checks passed'}" + (f", {len(warns)} warning(s)" if warns else ""))
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()
