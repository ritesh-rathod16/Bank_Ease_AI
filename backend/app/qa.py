"""Page-aware BM25 retrieval with extractive, abstaining answers. The document is never treated as instructions."""
import math, re
from collections import Counter

STOP = set("a an the is are was were be of to in on for and or what which who how does do did my me i it this that with as by at from can will shall there any".split())

def toks(s):
    out = []
    for w in re.findall(r"[a-z0-9%\.]+", re.sub(r"['’]s\b", "", s.lower())):  # drop possessives ("lender's" -> "lender")
        w = w.strip(".")
        if len(w) < 2 or w in STOP: continue
        out.append(w[:6] if len(w) > 6 else w.rstrip("s") if len(w) > 3 else w)
    return out

def chunks(pages):
    res = []
    for p, t in enumerate(pages, 1):
        lines = [l.strip() for l in t.split("\n") if l.strip()]
        for i in range(len(lines)):
            res.append({"page": p, "idx": i, "text": " ".join(lines[i:i + 2])})
    return res

def answer(question, pages, k=2, min_cover=0.5, min_score=0.8):
    q = list(dict.fromkeys(toks(question)))
    cs = chunks(pages)
    nf = {"found": False, "answer": "Not found in the uploaded document.", "sources": [], "mode": "extractive-fallback"}
    if not q or not cs: return nf
    docs = [Counter(toks(c["text"])) for c in cs]
    N, avg = len(cs), sum(sum(d.values()) for d in docs) / len(cs)
    df = {t: sum(1 for d in docs if t in d) for t in q}
    scored = []
    for c, d in zip(cs, docs):
        ln = sum(d.values()); s = 0.0; cov = 0
        for t in q:
            if d[t]:
                cov += 1
                idf = math.log(1 + (N - df[t] + .5) / (df[t] + .5))
                s += idf * d[t] * 2.2 / (d[t] + 1.2 * (0.25 + 0.75 * ln / avg))
        if cov / len(q) >= min_cover and s >= min_score:
            scored.append((s, c))
    scored.sort(key=lambda x: -x[0])
    picks = []
    for s, c in scored:
        if all(c["page"] != p["page"] or abs(c["idx"] - p["idx"]) > 1 for _, p in picks):
            picks.append((s, c))
        if len(picks) == k: break
    if not picks: return nf
    return {"found": True, "mode": "extractive-fallback",
            "answer": "The document states: " + " … ".join(f'"{c["text"]}" (page {c["page"]})' for _, c in picks),
            "sources": [{"page": c["page"], "text": c["text"], "score": round(s, 2)} for s, c in picks]}
