"""Page-aware BM25 retrieval with extractive, abstaining answers. The document is never treated as instructions."""
import math, os, re
from collections import Counter

STOP = set("a an the is are was were be of to in on for and or what which who how does do did my me i it this that with as by at from can will shall there any about say state stated list listed contain contains include includes provide provides mention mentioned".split())

def toks(s):
    out = []
    for w in re.findall(r"[a-z0-9%\.]+", re.sub(r"['’]s\b", "", s.lower())):  # drop possessives ("lender's" -> "lender")
        w = w.strip(".")
        if len(w) < 2 or w in STOP: continue
        if w.startswith(("exclu", "exclud")):
            out.append("exclu")
        else:
            out.append(w[:6] if len(w) > 6 else w.rstrip("s") if len(w) > 3 else w)
    return out


def _threshold(value, env_name, default, lower, upper):
    if value is not None:
        return value
    try:
        configured = float(os.getenv(env_name, str(default)))
    except ValueError:
        return default
    return min(max(configured, lower), upper)


def _has_exact_query_phrase(query_tokens, passage):
    """Treat an exact adjacent query phrase as strong evidence when BM25 is sparse."""
    if len(query_tokens) < 2:
        return False
    passage_tokens = toks(passage)
    query_pairs = set()
    for left, right in zip(query_tokens, query_tokens[1:]):
        query_pairs.add((left, right))
        query_pairs.add((right, left))
    return any(tuple(passage_tokens[index:index + 2]) in query_pairs
               for index in range(len(passage_tokens) - 1))

def chunks(pages):
    res = []
    for p, t in enumerate(pages, 1):
        lines = [l.strip() for l in t.split("\n") if l.strip()]
        index = 0
        chunk_index = 0
        while index < len(lines):
            next_is_heading = (index + 1 < len(lines) and len(lines[index + 1]) <= 100
                               and lines[index + 1].isupper()
                               and any(character.isalpha() for character in lines[index + 1]))
            end = index + 1
            if index + 1 < len(lines) and not next_is_heading:
                # Keep headings with the passage that gives them meaning. Other
                # content is grouped in non-overlapping pairs to avoid duplicate hits.
                end = index + 2
            res.append({"page": p, "idx": chunk_index, "text": " ".join(lines[index:end])})
            chunk_index += 1
            index = end
    return res

def answer(question, pages, k=2, min_cover=None, min_score=None):
    min_cover = _threshold(min_cover, "AI_RETRIEVAL_MIN_COVER", 0.5, 0.0, 1.0)
    min_score = _threshold(min_score, "AI_RETRIEVAL_MIN_SCORE", 0.8, 0.0, 1000000.0)
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
        phrase_match = _has_exact_query_phrase(q, c["text"])
        if cov / len(q) >= min_cover and (s >= min_score or phrase_match):
            scored.append((s + (1.0 if phrase_match else 0.0), c))
    scored.sort(key=lambda x: -x[0])
    picks = []
    for s, c in scored:
        if all(c["page"] != p["page"] or c["text"] != p["text"] for _, p in picks):
            picks.append((s, c))
        if len(picks) == k: break
    if not picks: return nf
    return {"found": True, "mode": "extractive-fallback",
            "answer": "The document states: " + " … ".join(f'"{c["text"]}" (page {c["page"]})' for _, c in picks),
            "sources": [{"page": c["page"], "text": c["text"], "score": round(s, 2)} for s, c in picks]}


def answer_sections(question, sections, k=2, min_cover=None, min_score=None):
    """Retrieve text from formats whose extracted layout has sections, not trustworthy pages."""
    result = answer(question, sections, k=k, min_cover=min_cover, min_score=min_score)
    if not result["found"]:
        return result
    for source in result["sources"]:
        source["section"] = source.pop("page")
        source["page"] = None
        source["location"] = f"extracted text section {source['section']} (page layout unavailable)"
    result["answer"] = re.sub(r"\(page (\d+)\)", r"(extracted text section \1; page layout unavailable)", result["answer"])
    return result


def answer_documents(question, documents, k=4, min_cover=None, min_score=None):
    """Retrieve supporting passages across an already-authorized document set."""
    if any(phrase in question.lower() for phrase in [
        "summarize", "summary", "plain english", "simple language", "explain this document",
        "important terms", "what should i be careful about", "key terms", "what are the important terms"
    ]):
        summary_sources = []
        for document in documents:
            for page_number, page_text in enumerate(document.get("pages", [])[:12], 1):
                for chunk in chunks([page_text])[:3]:
                    summary_sources.append({
                        "document_id": document["id"],
                        "filename": document.get("filename") or "Document",
                        "page": chunk.get("page"),
                        "location": f"page {page_number}",
                        "text": chunk["text"],
                        "score": 1.0,
                    })
                    if len(summary_sources) >= 20:
                        return {"found": True, "answer": "Document context loaded for summary generation.", "sources": summary_sources, "mode": "document-summary-context"}
        if summary_sources:
            return {"found": True, "answer": "Document context loaded for summary generation.", "sources": summary_sources, "mode": "document-summary-context"}

    min_cover = _threshold(min_cover, "AI_RETRIEVAL_MIN_COVER", 0.5, 0.0, 1.0)
    min_score = _threshold(min_score, "AI_RETRIEVAL_MIN_SCORE", 0.8, 0.0, 1000000.0)
    q = list(dict.fromkeys(toks(question)))
    cs = []
    for document in documents:
        for chunk in chunks(document.get("pages", [])):
            chunk.update({"document_id": document["id"], "filename": document.get("filename") or "Document"})
            if document.get("source_locator_type") == "section":
                chunk["section"] = chunk.pop("page")
                chunk["page"] = None
                chunk["location"] = f"extracted text section {chunk['section']} (page layout unavailable)"
            cs.append(chunk)
    not_found = {
        "found": False, "answer": "Not found in the selected documents.",
        "sources": [], "mode": "extractive-fallback",
    }
    if not q or not cs:
        return not_found
    docs = [Counter(toks(chunk["text"])) for chunk in cs]
    total_chunks = len(cs)
    average_length = sum(sum(counter.values()) for counter in docs) / total_chunks
    if average_length <= 0:
        return not_found
    document_frequency = {token: sum(token in counter for counter in docs) for token in q}
    scored = []
    for chunk, counter in zip(cs, docs):
        length = sum(counter.values())
        score = 0.0
        coverage = 0
        for token in q:
            if counter[token]:
                coverage += 1
                inverse_frequency = math.log(1 + (total_chunks - document_frequency[token] + .5)
                                             / (document_frequency[token] + .5))
                score += inverse_frequency * counter[token] * 2.2 / (
                    counter[token] + 1.2 * (0.25 + 0.75 * length / average_length)
                )
        phrase_match = _has_exact_query_phrase(q, chunk["text"])
        if coverage / len(q) >= min_cover and (score >= min_score or phrase_match):
            scored.append((score + (1.0 if phrase_match else 0.0), chunk))
    scored.sort(key=lambda item: (-item[0], item[1]["document_id"],
                                  item[1].get("page") or item[1].get("section", 0), item[1]["idx"]))
    picks = []
    for score, chunk in scored:
        if all(
            chunk["document_id"] != prior["document_id"]
            or (chunk.get("page"), chunk.get("section"), chunk["text"])
            != (prior.get("page"), prior.get("section"), prior["text"])
            for _, prior in picks
        ):
            picks.append((score, chunk))
        if len(picks) == k:
            break
    if not picks:
        return not_found
    sources = [{
        "document_id": chunk["document_id"],
        "filename": chunk["filename"],
        "page": chunk["page"],
        **({"section": chunk["section"], "location": chunk["location"]} if chunk.get("section") else {}),
        "text": chunk["text"],
        "score": round(score, 2),
    } for score, chunk in picks]
    citations = []
    for source in sources:
        location = source.get("location") or f"page {source['page']}"
        citations.append(f'- "{source["text"]}" ({source["filename"]}, {location})')
    answer_text = "The selected documents state:\n" + "\n".join(citations)
    return {"found": True, "mode": "extractive-fallback", "answer": answer_text, "sources": sources}
