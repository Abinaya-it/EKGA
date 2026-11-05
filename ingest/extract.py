"""FR-2: extract nodes/edges (with chunk provenance) from chunks into a per-document staging file.
Rule-based extractors cover the structured parts; `llm_extract` (optional) adds free-text relations when an LLM is configured.
Unresolved component names are emitted as ('Component', '~<surface>'); the loader maps them through entity resolution."""
import re, json
from config import STAGING

ROW_SPEC = re.compile(r"^(?P<name>[^|]+?)\s*\|\s*(?P<val>\d+(?:\.\d+)?)\s*Nm$")
ROW_PART = re.compile(r"^(?P<id>P-\d+)\s*\|\s*(?P<desc>[^|]+?)\s*\|\s*(?P<qty>\d+)$")
ROW_AFFECT = re.compile(r"^(?P<id>[A-Z]+-[A-Z0-9-]+)\s*\|\s*(?P<kind>Document|Part)$")
SUPERSEDE = re.compile(r"(ECN-\d+) supersedes part (P-\d+).*? with part (P-\d+)")
PROC = re.compile(r"^Procedure (MP-[\w-]+): (?:Replace )?(.+)$")
REFS = re.compile(r"^References: (.+)$")
COMPLY = re.compile(r"complies with (.+?)\.?$")
DOCID = re.compile(r"\b(?:MAN|SPC|ECN|BUL)-[A-Z0-9-]+\b")


def extract_doc(meta, chunks):
    nodes, edges = {}, []
    N = lambda label, id_, **p: nodes.setdefault((label, id_), {"label": label, "id": id_}).update(p)
    E = lambda s, rel, o, chunk, **p: edges.append({"s": list(s), "rel": rel, "o": list(o), "props": {"chunk": chunk, **p}})
    did, models = meta["id"], meta["models"]
    rev = meta["rev"] if meta["rev"].isdigit() else None
    N("Document", did, type=meta["type"], effective=meta["effective"], title=meta["title"], models=models, file=meta["file"])
    if meta["type"] == "ECN": N("ECN", did, effective=meta["effective"], models=models); E(("ECN", did), "DOCUMENTED_IN", ("Document", did), did)
    for m in models:
        N("Model", m); E(("Document", did), "APPLIES_TO", ("Model", m), did)
        if rev:
            rid = f"{m}-R{rev}"; N("Revision", rid, model=m, num=int(rev))
            if meta["type"] == "Manual": N("Revision", rid, effective=meta["effective"])
            E(("Document", did), "APPLIES_TO", ("Revision", rid), did)
    if meta.get("supersedes"): E(("Document", meta["supersedes"]), "SUPERSEDED_BY", ("Document", did), did)

    for c in chunks:
        cid, sec = c["id"], f"{did}#{c['section_num']}"
        if c["section_num"] != "0":
            N("Section", sec, title=c["section_title"], doc=did, page=c["page"]); E(("Document", did), "HAS_SECTION", ("Section", sec), cid)
        for ln in c["body"].split("\n"):
            ln = ln.strip()
            if (m := ROW_SPEC.match(ln)) and meta["type"] in ("Manual", "Bulletin") and rev:
                surf = m["name"].strip()
                if surf.lower() == "component": continue
                comp = ("Component", "~" + surf); sid = f"SPEC:{did}:{surf}"
                N("Spec", sid, value=float(m["val"]), unit="Nm", effective=meta["effective"], doc=did, section=c["section"], page=c["page"], rev=rev, model=models[0])
                N("Component", "~" + surf)
                E(("Spec", sid), "FOR_COMPONENT", comp, cid); E(("Spec", sid), "DOCUMENTED_IN", ("Document", did), cid)
                E(("Revision", f"{models[0]}-R{rev}"), "HAS_SPEC", ("Spec", sid), cid); E(("Model", models[0]), "HAS_COMPONENT", comp, cid)
            elif (m := ROW_PART.match(ln)) and meta["type"] == "Manual" and rev:
                N("Part", m["id"], desc=m["desc"]); E(("Revision", f"{models[0]}-R{rev}"), "USES_PART", ("Part", m["id"]), cid)
            elif (m := ROW_AFFECT.match(ln)) and meta["type"] == "ECN":
                tgt = ("Document", m["id"]) if m["kind"] == "Document" else ("Part", m["id"])
                N(*tgt); E(("ECN", did), "AFFECTS", tgt, cid)
            elif m := SUPERSEDE.search(ln):
                e, old, new = m.groups(); N("Part", old); N("Part", new)
                E(("Part", old), "SUPERSEDED_BY", ("Part", new), cid, ecn=e, effective=meta["effective"])
            elif m := PROC.match(ln):
                N("Procedure", m[1], title=ln.split(": ", 1)[1], doc=did); E(("Procedure", m[1]), "DOCUMENTED_IN", ("Document", did), cid)
                E(("Procedure", m[1]), "FOR_COMPONENT", ("Component", "~" + m[2].strip()), cid); N("Component", "~" + m[2].strip())
            elif m := REFS.match(ln):
                for ref in DOCID.findall(m[1]): E(("Document", did), "REFERENCES", ("Document", ref), cid)
            elif (m := COMPLY.search(ln)) and meta["type"] == "Spec":
                for s in m[1].split(";"):
                    s = s.strip()
                    if s: N("Standard", s); E(("Model", models[0]), "COMPLIES_WITH", ("Standard", s), cid)
    # referenced-but-unseen documents become stub nodes (keeps edges valid; they get filled when ingested)
    for e in edges:
        for end in (e["s"], e["o"]):
            nodes.setdefault(tuple(end), {"label": end[0], "id": end[1]})
    return {"doc_id": did, "nodes": list(nodes.values()), "edges": edges}


def llm_extract(chunk_text):
    """Optional: ask a local LLM for extra triples from free text (used for fine-tuning data & prose-heavy corpora)."""
    from agents import llm
    out = llm.chat([{"role": "system", "content": "Extract (subject, relation, object) triples. Relations: SUPERSEDED_BY, AFFECTS, REFERENCES, COMPLIES_WITH, USES_PART. Return JSON list only."},
                    {"role": "user", "content": chunk_text}], json_mode=True)
    try: return json.loads(out) if out else []
    except Exception: return []


def save(staged):
    (STAGING / f"{staged['doc_id']}.json").write_text(json.dumps(staged))
