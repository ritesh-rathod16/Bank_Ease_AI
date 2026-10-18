import json, os, re, pathlib, time
ROOT = pathlib.Path(os.getenv("BANKEASE_DATA", pathlib.Path(__file__).resolve().parents[2] / "data" / "runtime"))
ID = re.compile(r"^[0-9a-f]{32}$")

def _p(i, ext):
    if not ID.match(i): raise KeyError(i)
    return ROOT / ("uploads" if ext == "pdf" else "records") / f"{i}.{ext}"

def save(rec):
    d = ROOT / "records"; d.mkdir(parents=True, exist_ok=True, mode=0o700)
    f = _p(rec["id"], "json"); f.write_text(json.dumps(rec)); os.chmod(f, 0o600)

def load(i):
    p = _p(i, "json")
    if not p.exists(): raise KeyError(i)
    return json.loads(p.read_text())

def pdf_path(i):
    (ROOT / "uploads").mkdir(parents=True, exist_ok=True)
    return _p(i, "pdf")

def delete(i):
    for e in ("json", "pdf"):
        _p(i, e).unlink(missing_ok=True)

def purge(hours):
    """Delete records/uploads older than `hours` (0 or None disables). Returns number of files removed."""
    if not hours or hours <= 0: return 0
    cutoff, n = time.time() - hours * 3600, 0
    for sub in ("records", "uploads"):
        d = ROOT / sub
        for f in (d.iterdir() if d.exists() else []):
            if f.is_file() and f.stat().st_mtime < cutoff: f.unlink(); n += 1
    return n
