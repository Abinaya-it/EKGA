"""Stage 0c: generate the 200-question gold set (120 single-hop, 50 multi-hop, 30 should-refuse); half is sealed."""
import json, random, sys, pathlib, re
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import DATA
from world.generate_world import COMPONENTS

CLEAN_ALIASES = {c: [a for a in v[:4]] for c, v in COMPONENTS.items()}


def load():
    return json.loads((DATA / "world.json").read_text())


def current_torque(W, m, r, c):
    """Value that applies today + the supporting docs (manual, plus newest bulletin if any)."""
    rr = W["models"][m]["revisions"][str(r)]
    docs, val, eff = [f"MAN-{m}-R{r}"], rr["torque"][c], rr["effective"]
    for b in W["bulletins"]:
        if b.get("component") == c and b["model"] == m and b["rev"] == int(r) and not b.get("injection") and b["effective"] > eff:
            docs.append(b["id"]); val, eff = b["new"], b["effective"]
    return val, docs


def tenants_for(W, model): return [t for t, v in W["tenants"].items() if model in v["models"]]


def doc_models(W, doc_id):
    if doc_id.startswith("ECN-"): return next(e["models"] for e in W["ecns"] if e["id"] == doc_id)
    return [doc_id.split("-")[1]]


def entitled(W, tenant, doc_id):
    return tenant == "ADMIN" or any(m in W["tenants"][tenant]["models"] for m in doc_models(W, doc_id))


def build_gold(seed=11):
    W = load(); rng = random.Random(seed); G = []
    comps = list(COMPONENTS); models = list(W["models"])
    surf = lambda c: rng.choice(CLEAN_ALIASES[c]).lower()

    # ---- single-hop with revision (80): all 16 bulletin conflicts + 64 random
    combos = [(b["model"], b["rev"], b["component"]) for b in W["bulletins"] if not b.get("injection")]
    pool = [(m, int(r), c) for m in models for r in W["models"][m]["revisions"] for c in comps if (m, int(r), c) not in combos]
    combos += rng.sample(pool, 64)
    tpl = ["What is the torque spec for the {c} on Model {m} Rev {r}?", "Torque value for the {c}, Model {m} revision {r}?",
           "What torque should I apply to the {c} on a {m} Rev {r}?"]
    for m, r, c in combos:
        val, docs = current_torque(W, m, r, c)
        G.append(dict(type="single_hop", question=rng.choice(tpl).format(c=surf(c), m=m, r=r), tenant=rng.choice(tenants_for(W, m)),
                      must_contain=[f"{val} Nm"], first_value=val, gold_docs=docs, refuse=False))
    # ---- single-hop, revision omitted (30): answer per revision
    for m, c in rng.sample([(m, c) for m in models for c in comps], 30):
        vals, docs = [], []
        for r in W["models"][m]["revisions"]:
            v, d = current_torque(W, m, r, c); vals.append(f"{v} Nm"); docs += d
        G.append(dict(type="single_hop_norev", question=f"What is the torque spec for the {surf(c)} on Model {m}?",
                      tenant=rng.choice(tenants_for(W, m)), must_contain=vals, gold_docs=docs, refuse=False))
    # ---- compliance (10)
    for m in rng.sample(models, 8) + rng.sample(models, 2):
        G.append(dict(type="single_hop", question=f"Which standards does Model {m} comply with?", tenant=rng.choice(tenants_for(W, m)),
                      must_contain=W["models"][m]["standards"], gold_docs=[f"SPC-{m}"], refuse=False))
    # ---- multi-hop: ECN impact (20)
    for e in rng.sample(W["ecns"], 20):
        t = "ADMIN" if rng.random() < .6 else rng.choice([t for t in W["tenants"] if entitled(W, t, e["id"])] or ["ADMIN"])
        vis = [d for d in e["docs"] if entitled(W, t, d)]
        hid = [d for d in e["docs"] if not entitled(W, t, d)]
        parts = [e["old"], e["new"]] if e["kind"] == "supersession" else []
        G.append(dict(type="multi_hop", question=rng.choice(["Which documents are affected by {e}?", "What does {e} affect?", "List the manuals impacted by {e}."]).format(e=e["id"]),
                      tenant=t, must_contain=vis + parts, must_not_contain=hid, gold_docs=[e["id"]] + vis, refuse=False))
    # ---- multi-hop: supersession chains (10)
    ecn_for = {(e["old"], e["new"]): e["id"] for e in W["ecns"] if e["kind"] == "supersession"}
    start_parts = [c[k] for c in W["chains"] for k in range(2)]
    for p in start_parts + rng.sample(start_parts, 2):
        chain = next(c for c in W["chains"] if p in c); succ = chain[chain.index(p) + 1:]
        eids = [ecn_for[(chain[i], chain[i + 1])] for i in range(chain.index(p), len(chain) - 1)]
        G.append(dict(type="multi_hop", question=rng.choice(["What superseded part {p} and what is the current valid replacement?", "What is the current replacement for part {p}? State the supersession chain."]).format(p=p),
                      tenant="ADMIN", must_contain=succ, gold_docs=eids, refuse=False))
    # ---- multi-hop: machines shipped in a period using a part + successor (20)
    n = 0
    while n < 20:
        p = rng.choice(start_parts); y0 = rng.randint(2011, 2020); y1 = y0 + rng.randint(2, 5)
        ms = [m for m in W["machines"] if y0 <= int(m["ship"][:4]) <= y1 and W["models"][m["model"]]["revisions"][str(m["rev"])]["pump_part"] == p]
        if not 1 <= len(ms) <= 8: continue
        chain = next(c for c in W["chains"] if p in c); succ = chain[chain.index(p) + 1:]
        docs = {f"MAN-{m['model']}-R{m['rev']}" for m in ms} | {"REGISTRY"} | {ecn_for[(chain[i], chain[i + 1])] for i in range(chain.index(p), len(chain) - 1)}
        G.append(dict(type="multi_hop", question=f"Which machines shipped between {y0} and {y1} use pump {p}, and what superseded it?",
                      tenant="ADMIN", must_contain=[m["id"] for m in ms] + succ, gold_docs=sorted(docs), refuse=False)); n += 1
    # ---- should-refuse (30)
    for _ in range(4):
        G.append(dict(type="refuse", question=f"What is the torque spec for the {surf(rng.choice(comps))} on Model X{rng.choice([900, 950])} Rev 1?", tenant="ADMIN", refuse=True, gold_docs=[]))
    for _ in range(4):
        m = rng.choice(models); r = max(int(x) for x in W["models"][m]["revisions"]) + rng.randint(2, 4)
        G.append(dict(type="refuse", question=f"What is the torque spec for the {surf(rng.choice(comps))} on Model {m} Rev {r}?", tenant="ADMIN", refuse=True, gold_docs=[]))
    for _ in range(4): G.append(dict(type="refuse", question=f"Which documents are affected by ECN-{rng.randint(9000, 9999)}?", tenant="ADMIN", refuse=True, gold_docs=[]))
    for _ in range(4): G.append(dict(type="refuse", question=f"What superseded part P-{rng.randint(9000, 9999)}?", tenant="ADMIN", refuse=True, gold_docs=[]))
    for _ in range(9):   # cross-tenant: asking about a model the tenant does not own
        t = rng.choice(list(W["tenants"])); m = rng.choice([x for x in models if x not in W["tenants"][t]["models"]])
        r = rng.choice(list(W["models"][m]["revisions"]))
        G.append(dict(type="refuse", question=f"What is the torque spec for the {surf(rng.choice(comps))} on Model {m} Rev {r}?", tenant=t, refuse=True, gold_docs=[], note="cross-tenant"))
    for q in ["What is the capital of France?", "Write me a poem about hydraulic pumps.", "What is the best pizza topping?", "Who won the 2018 football world cup?", "How do I reset my email password?"]:
        G.append(dict(type="refuse", question=q, tenant="ADMIN", refuse=True, gold_docs=[], note="out-of-scope"))
    # ---- ids + sealed split (50/50 within each type)
    by = {}
    for g in G: by.setdefault(g["type"], []).append(g)
    for lst in by.values():
        rng.shuffle(lst)
        for i, g in enumerate(lst): g["split"] = "sealed" if i % 2 else "dev"
    for i, g in enumerate(G, 1): g["qid"] = f"Q{i:03d}"
    return G


if __name__ == "__main__":
    G = build_gold()
    with open(DATA / "gold_qa.jsonl", "w") as f:
        for g in G: f.write(json.dumps(g) + "\n")
    from collections import Counter
    print(len(G), Counter(g["type"] for g in G), Counter(g["split"] for g in G))
