"""FR-4 data: (query, positive chunk, hard negative) triplets for embedding fine-tuning + text->Cypher SFT pairs.
Anything touching a sealed-gold (model, rev, component) or sealed ECN/part is excluded so the sealed set stays clean."""
import json, random, re, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import DATA
from graph.resolve import find_component
from world.generate_world import COMPONENTS

OUT = DATA / "finetune"; OUT.mkdir(exist_ok=True)
ROW = re.compile(r"^(?P<name>[^|]+?)\s*\|\s*(?P<val>\d+(?:\.\d+)?)\s*Nm$")


def main(seed=3):
    rng = random.Random(seed)
    gold = [json.loads(l) for l in open(DATA / "gold_qa.jsonl")]; W = json.loads((DATA / "world.json").read_text())
    comp_map = json.loads((DATA / "resolved_components.json").read_text()); chunks = [json.loads(l) for l in open(DATA / "chunks.jsonl")]
    sealed_txt = " ".join(g["question"] for g in gold if g["split"] == "sealed")
    spec = {}                                    # (model, rev, comp) -> spec-table chunk
    for c in chunks:
        if c["doc_type"] == "Manual" and c["section_title"] == "Torque Specifications":
            for ln in c["body"].split("\n"):
                m = ROW.match(ln.strip())
                if m: spec[(c["models"][0], int(c["rev"]), find_component(m["name"], comp_map))] = c
    tpl = ["torque spec {c} Model {m} Rev {r}", "what torque for the {c} on {m} revision {r}", "{m} rev {r} {c} tightening torque Nm", "how tight should the {c} be on a {m} Rev {r}"]
    trip = []
    for (m, r, comp), pos in spec.items():
        key = f"{m} Rev {r}"
        if any(key in g["question"] and comp.split()[0] in g["question"].lower() for g in gold if g["split"] == "sealed"): continue
        negs = [c for (m2, r2, c2), c in spec.items() if c2 == comp and (m2, r2) != (m, r)]            # same component, wrong model/revision = hard negative
        for t in rng.sample(tpl, 2):
            trip.append({"query": t.format(c=rng.choice(COMPONENTS[comp][:4] if comp in COMPONENTS else [comp]).lower(), m=m, r=r), "positive": pos["text"], "negative": rng.choice(negs)["text"]})
    with open(OUT / "triplets.jsonl", "w") as f:
        for t in trip: f.write(json.dumps(t) + "\n")
    # ---- text->Cypher SFT pairs (templates identical to graph.store.Neo4jGraph)
    T = {"ecn": "MATCH (e:ECN {id:$id})-[r:AFFECTS]->(t) RETURN labels(t)[0] AS label, t.id AS id, r.chunk AS chunk, t.models AS models LIMIT 200",
         "chain": "MATCH p=(a:Part {id:$id})-[:SUPERSEDED_BY*1..6]->(b:Part) WITH p ORDER BY length(p) DESC LIMIT 1 RETURN [n IN nodes(p) | n.id] AS ids",
         "mach": "MATCH (m:Machine)-[:INSTANCE_OF]->(rv:Revision)-[u:USES_PART]->(p:Part {id:$id}) WHERE m.ship_year >= $y0 AND m.ship_year <= $y1 AND ($all OR m.tenant = $tenant) RETURN m.id AS id LIMIT 200"}
    held = set(re.findall(r"ECN-\d+|P-\d{4}", " ".join(g["question"] for g in gold if g["split"] == "sealed")))
    sft = []
    for e in W["ecns"]:
        if e["id"] in held: continue
        for q in (f"Which documents are affected by {e['id']}?", f"What does {e['id']} impact?"): sft.append({"question": q, "cypher": T["ecn"], "params": {"id": e["id"]}})
    for c in W["chains"]:
        for p in c[:2]:
            if p in held: continue
            sft.append({"question": f"What superseded part {p}?", "cypher": T["chain"], "params": {"id": p}})
            for _ in range(3):
                y0 = rng.randint(2011, 2020); y1 = y0 + rng.randint(2, 5)
                sft.append({"question": f"Which machines shipped between {y0} and {y1} use pump {p}?", "cypher": T["mach"], "params": {"id": p, "y0": y0, "y1": y1, "all": True, "tenant": "ADMIN"}})
    with open(OUT / "cypher_sft.jsonl", "w") as f:
        for s in sft: f.write(json.dumps(s) + "\n")
    print(f"{len(trip)} triplets, {len(sft)} Cypher SFT pairs -> {OUT}")


if __name__ == "__main__": main()
