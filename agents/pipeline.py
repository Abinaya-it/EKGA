"""FR-3/FR-5: router -> planner -> retrieval/graph agents -> evidence combiner -> verification agent.
Answers are composed from verified evidence (deterministic), so every factual sentence carries citations and the run is reproducible.
The LLM (if configured) is used only for routing fallback, the optional judge pass, Cypher generation, and the baseline."""
import csv, json, re, time
from collections import OrderedDict
from config import DATA, SOURCE, VERIFY_LLM, GRAPH_BACKEND
from retrieval import index as ix
from graph.store import open_graph
from graph.resolve import find_component
from agents import llm

REFUSAL = "Cannot verify"
ROW = re.compile(r"^(?P<name>[^|]+?)\s*\|\s*(?P<val>\d+(?:\.\d+)?)\s*Nm$")
RE_MODEL, RE_ECN, RE_PART = re.compile(r"\bX\d{3}\b", re.I), re.compile(r"\bECN-\d+\b", re.I), re.compile(r"\bP-\d{4}\b", re.I)
RE_REV = re.compile(r"\brev(?:ision)?\.?\s*(\d+)\b", re.I)
RE_YEARS = re.compile(r"\b((?:19|20)\d{2})\b")
INJECTION = re.compile(r"ignore (all |any )?(previous|prior) instructions|reveal (your|the) system prompt|you are now", re.I)
TOKENS = re.compile(r"[A-Z]{1,4}-[A-Z0-9-]+|\d+(?:\.\d+)?")


class Trace(list):
    def step(self, name, t0, **kw): self.append({"step": name, "ms": round((time.time() - t0) * 1000, 1), **kw})


class Engine:
    def __init__(self):
        self.chunks = ix.load_chunks(); self.index = ix.load(); self.byid = {c["id"]: c for c in self.chunks}
        self.graph = open_graph()
        self.comp_map = json.loads((DATA / "resolved_components.json").read_text())
        self.tenants = json.loads((SOURCE / "tenants.json").read_text())
        self.reg = {}
        with open(SOURCE / "machines.csv") as f:
            for r in csv.DictReader(f):
                self.reg[f"REG:{r['serial']}"] = f"{r['serial']} | model {r['model']} | rev {r['rev']} | shipped {r['ship_date']} | tenant {r['tenant']}"
        self.sessions, self.cache, self.stats = {}, OrderedDict(), {"queries": 0, "cache_hits": 0, "llm_calls": 0}
        self.quarantined = {c["id"] for c in self.chunks if INJECTION.search(c["body"])}   # prompt-injection defense: data, never instructions

    # ------------------------------------------------------------------ helpers
    def allowed(self, tenant):
        if tenant not in self.tenants: raise PermissionError("unknown tenant")
        m = self.tenants[tenant]["models"]; return None if m == "*" else m

    def evidence(self, cid): return self.reg[cid] if cid.startswith("REG:") else self.byid[cid]["text"]

    def chunk_ok(self, cid, allowed):
        if cid.startswith("REG:"): return True
        return allowed is None or any(m in allowed for m in self.byid[cid]["models"])

    def cite(self, cid):
        if cid.startswith("REG:"): return {"chunk": cid, "doc": "REGISTRY", "section": "install base", "page": 0}
        c = self.byid[cid]; return {"chunk": cid, "doc": c["doc_id"], "section": c["section"], "page": c["page"]}

    def entities(self, q):
        revs = [int(x) for x in RE_REV.findall(q)]
        ys = [int(y) for y in RE_YEARS.findall(q)]
        return {"models": [m.upper() for m in RE_MODEL.findall(q)], "revs": sorted(set(revs)), "ecns": [e.upper() for e in RE_ECN.findall(q)],
                "parts": [p.upper() for p in RE_PART.findall(q)], "comp": find_component(q, self.comp_map),
                "years": (min(ys), max(ys)) if ys else None}

    def route(self, q, e, ctx=None):
        ql = q.lower()
        if e["ecns"] and re.search(r"affect|impact", ql): return "multi_hop"
        if e["parts"] and re.search(r"supersed|replac|successor|chain|current|ship|machine", ql): return "multi_hop"
        if e["comp"] and len(e["revs"]) >= 2 and re.search(r"compar|differ|versus|\bvs\b|change", ql): return "comparative"
        if re.search(r"torque|\bnm\b|spec|compl|standard", ql) or e["models"] or (e["comp"] and (ctx or {}).get("model")): return "lookup"
        if llm.available():
            out = llm.chat([{"role": "user", "content": "Classify as one word (lookup, multi_hop, out_of_scope) a question for a machinery service-manual assistant: " + q}], max_tokens=5)
            self.stats["llm_calls"] += 1
            if out and out.strip().lower() in ("lookup", "multi_hop"): return out.strip().lower()
        return "out_of_scope"

    def refuse(self, why="the available documents do not contain enough verified evidence for your account"):
        return [], f"{REFUSAL}: {why}."

    # ------------------------------------------------------------------ handlers -> (claims, preface)
    def h_torque(self, q, e, ctx, tenant, trace):
        model = e["models"][0] if e["models"] else ctx.get("model"); comp = e["comp"] or ctx.get("comp")
        if not model or not comp:
            return "clarify", f"Which {'model' if not model else 'component'} do you mean? Entitled models: {', '.join(self.graph.models() if self.allowed(tenant) is None else self.allowed(tenant))}."
        allowed = self.allowed(tenant)
        if model not in self.graph.models() or (allowed is not None and model not in allowed): return self.refuse()
        revs_all = self.graph.revisions(model)
        revs = e["revs"] if e["revs"] else ([ctx["rev"]] if (not e["models"] and ctx.get("rev")) else revs_all)
        revs = [r for r in revs if r in revs_all]
        if not revs: return self.refuse()
        t0 = time.time()
        idx = self.index.mask(allowed_models=allowed, model=model, types={"Manual", "Bulletin"})           # pre-filter
        hits = self.index.hybrid(f"{comp} torque {model} revision", idx, k=60)
        trace.step("retrieve.hybrid", t0, candidates=int(len(idx)), hits=len(hits), filter=f"model={model}")
        rows = {}
        for i in hits:
            c = self.chunks[i]
            if not c["rev"].isdigit() or int(c["rev"]) not in revs or c["id"] in self.quarantined: continue
            for ln in c["body"].split("\n"):
                m = ROW.match(ln.strip())
                if m and find_component(m["name"], self.comp_map) == comp:
                    rows.setdefault(int(c["rev"]), []).append({"v": float(m["val"]), "doc": c["doc_id"], "eff": c["effective"], "chunk": c["id"]})
        claims, vals = [], {}
        for r in sorted(rows):
            rs = sorted(rows[r], key=lambda x: x["eff"], reverse=True); cur = rs[0]; fmt = lambda v: f"{v:g}"
            txt = f"Rev {r}: the {comp} torque is {fmt(cur['v'])} Nm per {cur['doc']} (effective {cur['eff']})"
            cites = [cur["chunk"]]
            old = next((x for x in rs[1:] if x["v"] != cur["v"]), None)
            if old:
                txt += f"; this supersedes {fmt(old['v'])} Nm stated in {old['doc']} (effective {old['eff']})"; cites.append(old["chunk"])
            claims.append({"text": txt + ".", "cites": cites, "kind": "fact"}); vals[r] = (fmt(cur["v"]), cur["chunk"])
        if not claims: return self.refuse()
        if len(vals) >= 2 and len(e["revs"]) >= 2 and len(set(v for v, _ in vals.values())) > 1:
            (ra, (va, ca)), (rb, (vb, cb)) = list(vals.items())[0], list(vals.items())[-1]
            claims.append({"text": f"The {comp} torque differs between Rev {ra} ({va} Nm) and Rev {rb} ({vb} Nm).", "cites": [ca, cb], "kind": "fact"})
        ctx.update(model=model, comp=comp, rev=(revs[0] if len(revs) == 1 else None))
        return claims, ("" if e["revs"] or len(revs) == 1 else f"No revision was specified, so values are given per revision of Model {model}.")

    def h_compliance(self, q, e, ctx, tenant, trace):
        model = e["models"][0] if e["models"] else ctx.get("model"); allowed = self.allowed(tenant)
        if not model: return "clarify", "Which model do you mean?"
        if model not in self.graph.models() or (allowed is not None and model not in allowed): return self.refuse()
        t0 = time.time(); st = self.graph.standards(model); trace.step("graph.standards", t0, model=model)
        if not st: return self.refuse()
        ctx.update(model=model)
        return [{"text": f"Model {model} complies with {'; '.join(s['standard'] for s in st)}.", "cites": sorted({s["chunk"] for s in st}), "kind": "fact"}], ""

    def chain_claims(self, part, allowed, trace):
        t0 = time.time(); hops = self.graph.supersession_chain(part); trace.step("graph.supersession_chain", t0, part=part, hops=len(hops))
        claims = []
        for h in hops:
            if not self.chunk_ok(h["chunk"], allowed): break              # never reveal hops evidenced only by non-entitled documents
            claims.append({"text": f"Part {h['a']} was superseded by {h['b']} under {h['ecn']} (effective {h['effective']}).", "cites": [h["chunk"]], "kind": "fact"})
        if claims:
            last = hops[len(claims) - 1]
            claims.append({"text": f"The current valid part is {last['b']}.", "cites": [last["chunk"]], "kind": "fact"})
        else:
            claims.append({"text": f"No supersession of part {part} is recorded in the documents available to you.", "cites": [], "kind": "absence"})
        return claims

    def h_multi(self, q, e, ctx, tenant, trace):
        allowed, ql = self.allowed(tenant), q.lower()
        if e["parts"]:
            part = e["parts"][0]
            if not self.graph.exists("Part", part): return self.refuse()
            claims = []
            if re.search(r"ship|machine", ql):
                y0, y1 = e["years"] or (1900, 2100)
                t0 = time.time(); ms = self.graph.machines_using(part, y0, y1, tenant); trace.step("graph.machines_using", t0, part=part, years=[y0, y1], n=len(ms))
                for m in ms:
                    claims.append({"text": f"{m['id']} (Model {m['model']} Rev {m['rev']}, shipped {m['year']}) uses part {part}.", "cites": [m["use_chunk"], m["reg_chunk"]], "kind": "fact"})
                if not ms: claims.append({"text": f"No machines shipped {y0}-{y1} that use part {part} were found for your account.", "cites": [], "kind": "absence"})
            claims += self.chain_claims(part, allowed, trace)
            return claims, ""
        if e["ecns"]:
            ecn = e["ecns"][0]
            if not self.graph.exists("ECN", ecn): return self.refuse()
            doc = self.graph.doc(ecn)
            if allowed is not None and not any(m in allowed for m in doc["models"]): return self.refuse()
            t0 = time.time(); aff = [a for a in self.graph.ecn_affects(ecn, allowed) if a["visible"]]; trace.step("graph.ecn_affects", t0, ecn=ecn, n=len(aff))
            if not aff: return self.refuse()
            docs = sorted(a["id"] for a in aff if a["label"] == "Document"); parts = sorted(a["id"] for a in aff if a["label"] == "Part")
            txt = f"{ecn} affects documents {', '.join(docs) or 'none you are entitled to'}" + (f" and parts {', '.join(parts)}" if parts else "") + "."
            claims = [{"text": txt, "cites": sorted({a["chunk"] for a in aff}), "kind": "fact"}]
            for p in parts:
                hop = next(iter(self.graph.supersession_chain(p)), None)
                if hop and hop["ecn"] == ecn:
                    claims.append({"text": f"{ecn} supersedes part {hop['a']} with part {hop['b']} (effective {hop['effective']}).", "cites": [hop["chunk"]], "kind": "fact"})
            return claims, ""
        return self.refuse()

    # ------------------------------------------------------------------ verification agent
    def toks(self, s): return set(TOKENS.findall(s))

    def verify(self, claims, allowed, trace):
        t0 = time.time(); kept, removed = [], []
        for c in claims:
            if c["kind"] == "absence": kept.append(c); continue
            ok = bool(c["cites"]) and all(self.chunk_ok(x, allowed) for x in c["cites"])
            if ok:
                ev = " ".join(self.evidence(x) for x in c["cites"])
                ok = self.toks(c["text"]) <= self.toks(ev)                       # every id/number in the claim must appear in its cited passages
                if ok and VERIFY_LLM and llm.available():
                    out = llm.chat([{"role": "user", "content": f"Passage:\n{ev}\n\nClaim: {c['text']}\nDoes the passage support the claim? Answer yes or no."}], max_tokens=3)
                    self.stats["llm_calls"] += 1; ok = bool(out and out.strip().lower().startswith("y"))
            (kept if ok else removed).append(c)
        trace.step("verify", t0, kept=len(kept), removed=len(removed))
        return kept, removed

    # ------------------------------------------------------------------ main entry
    def answer(self, q, tenant="ADMIN", session=None):
        t_start = time.time(); self.stats["queries"] += 1; trace = Trace(); allowed = self.allowed(tenant)
        ctx = self.sessions.setdefault(session or "_anon", {}) if session else {}
        key = (tenant, re.sub(r"\s+", " ", q.lower().strip()))
        if session is None and key in self.cache:                                           # semantic cache (exact-normalized here; swap for embedding match)
            self.stats["cache_hits"] += 1; r = dict(self.cache[key]); r["cached"] = True; return r
        t0 = time.time(); e = self.entities(q); route = self.route(q, e, ctx); trace.step("router", t0, route=route, entities={k: v for k, v in e.items() if v})
        claims, preface, removed = [], "", []
        if route == "out_of_scope": claims, preface = self.refuse("this question is outside the engineering document corpus")
        else:
            t0 = time.time()
            if route == "multi_hop": out = self.h_multi(q, e, ctx, tenant, trace)
            elif re.search(r"compl|standard", q.lower()) and not e["comp"]: out = self.h_compliance(q, e, ctx, tenant, trace)
            else: out = self.h_torque(q, e, ctx, tenant, trace)
            trace.step("plan+execute", t0, route=route)
            claims, preface = out
            if claims == "clarify": return self._resp(preface, route, False, [], [], trace, t_start, clarify=True)
            if claims: claims, removed = self.verify(claims, allowed, trace)
        refused = not any(c["kind"] == "fact" for c in claims) and not any(c["kind"] == "absence" for c in claims)
        if refused:
            if claims == [] and not preface.startswith(REFUSAL): preface = f"{REFUSAL}: no claim could be verified against its cited passage."
            return self._resp(preface, route, True, [], removed, trace, t_start)
        lines, cites = [], OrderedDict()
        for c in claims:
            tags = []
            for cid in c["cites"]:
                ci = self.cite(cid); cites[cid] = ci; tags.append(f"[{ci['doc']} §{ci['section'].split()[0]} p.{ci['page']}]" if ci["page"] else f"[{ci['doc']}]")
            lines.append(f"- {c['text']} {' '.join(tags)}".rstrip())
        text = (preface + "\n" if preface else "") + "\n".join(lines)
        resp = self._resp(text, route, False, list(cites.values()), removed, trace, t_start, claims=claims)
        if session is None: self.cache[key] = resp; self.cache.move_to_end(key); [self.cache.popitem(last=False) for _ in range(max(0, len(self.cache) - 500))]
        return resp

    def _resp(self, text, route, refused, cites, removed, trace, t_start, claims=None, clarify=False):
        return {"answer": text, "route": route, "refused": refused, "clarify": clarify, "citations": cites, "claims": claims or [],
                "removed_claims": len(removed), "trace": list(trace), "latency_ms": round((time.time() - t_start) * 1000, 1), "cached": False,
                "llm_calls": self.stats["llm_calls"]}


class Baseline(Engine):
    """Stage-1 baseline: dense-only top-5 over entitled chunks, no revision filter, no graph. Extractive unless an LLM is configured."""
    def answer(self, q, tenant="ADMIN", session=None):
        t_start = time.time(); trace = Trace(); allowed = self.allowed(tenant)
        idx = self.index.mask(allowed_models=allowed)
        hits = self.index.vector_only(q, idx, k=5)
        if not hits or self.index.dense(q, idx, 1)[0][1] < 0.12:
            return self._resp(f"{REFUSAL}: no relevant passage found.", "baseline", True, [], [], trace, t_start)
        comp = find_component(q, self.comp_map)
        if llm.available():
            ctx = "\n\n".join(self.chunks[i]["text"] for i in hits if self.chunks[i]["id"] not in self.quarantined)
            out = llm.chat([{"role": "system", "content": "Answer ONLY from the context and cite [DOC-ID p.N]. If unsure say 'Cannot verify'."},
                            {"role": "user", "content": f"Context:\n{ctx}\n\nQuestion: {q}"}], max_tokens=300)
            if out: return self._resp(out, "baseline", out.lower().startswith("cannot verify"), [self.cite(self.chunks[i]["id"]) for i in hits[:2]], [], trace, t_start)
        for i in hits:                                                        # extractive: first row mentioning the component, in rank order
            c = self.chunks[i]
            for ln in c["body"].split("\n"):
                m = ROW.match(ln.strip())
                if comp and m and find_component(m["name"], self.comp_map) == comp:
                    return self._resp(f"{comp}: {m['val']} Nm [{c['doc_id']} p.{c['page']}]", "baseline", False, [self.cite(c["id"])], [], trace, t_start)
        c = self.chunks[hits[0]]
        return self._resp(f"{c['body'][:400]} [{c['doc_id']} p.{c['page']}]", "baseline", False, [self.cite(c["id"])], [], trace, t_start)
