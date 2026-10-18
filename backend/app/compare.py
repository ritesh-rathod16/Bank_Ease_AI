import re
KEYS = ["loan_amount", "interest_rate", "tenure", "emi", "processing_fee"]

def _norm(k, f):
    if f["value"] is None: return None
    if k == "tenure": k = "tenure_months"
    if isinstance(f["value"], (int, float)): return float(f["value"])
    n = re.search(r"[\d,]+(?:\.\d+)?", str(f["value"]))
    return (float(n.group().replace(",", "")), "%" in str(f["value"])) if n else str(f["value"]).lower()

def compare(a, b):
    rows = []
    for k in KEYS:
        fa, fb = a.get(k), b.get(k)
        va, vb = (_norm(k, fa) if fa else None), (_norm(k, fb) if fb else None)
        if va is None or vb is None: st = "needs_review" if "review" in (fa and fa["status"], fb and fb["status"]) else "not_found"
        elif "review" in (fa["status"], fb["status"]): st = "needs_review"
        elif isinstance(va, tuple) and va[1] != vb[1]: st = "not_comparable"
        else: st = "match" if va == vb else "potential_difference"
        rows.append({"field": k, "status": st,
                     "a": {"value": fa and fa["raw"], "page": fa and fa["page"]},
                     "b": {"value": fb and fb["raw"], "page": fb and fb["page"]}})
    return rows
