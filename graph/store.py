"""FR-2 graph layer. build_graph_data(): idempotent rebuild from staging. Two interchangeable backends:
MemGraph (networkx, zero infrastructure) and Neo4jGraph (read-only, guarded, parameterized Cypher templates)."""
import csv, json, glob
import networkx as nx
from config import STAGING, SOURCE, DATA, GRAPH_BACKEND, NEO4J_URI, NEO4J_RO_USER, NEO4J_RO_PASSWORD, NEO4J_USER, NEO4J_PASSWORD

NODE_LABELS = ["Machine", "Model", "Revision", "Component", "Part", "Document", "Section", "ECN", "Spec", "Procedure", "Standard"]
REL_TYPES = ["HAS_COMPONENT", "USES_PART", "SUPERSEDED_BY", "AFFECTS", "DOCUMENTED_IN", "APPLIES_TO", "REFERENCES", "COMPLIES_WITH",
             "HAS_SPEC", "FOR_COMPONENT", "HAS_SECTION", "INSTANCE_OF"]


def build_graph_data(comp_map):
    nodes, edges = {}, {}
    fix = lambda end: (end[0], comp_map.get(end[1][1:], end[1][1:]) if end[0] == "Component" and end[1].startswith("~") else end[1])
    for f in sorted(glob.glob(str(STAGING / "*.json"))):
        st = json.loads(open(f).read())
        for n in st["nodes"]:
            lab, id_ = fix((n["label"], n["id"]))
            nodes.setdefault((lab, id_), {"label": lab, "id": id_}).update({k: v for k, v in n.items() if k not in ("label", "id")})
        for e in st["edges"]:
            s, o = fix(e["s"]), fix(e["o"])
            edges.setdefault((s, e["rel"], o), {"s": list(s), "rel": e["rel"], "o": list(o), "props": e["props"]})
    for (lab, id_), n in list(nodes.items()):
        if lab == "Component":
            n["aliases"] = sorted({k for k, v in comp_map.items() if v == id_})
    with open(SOURCE / "machines.csv") as f:           # install-base registry (structured source)
        for r in csv.DictReader(f):
            mid, rid = r["serial"], f"{r['model']}-R{r['rev']}"
            nodes[("Machine", mid)] = {"label": "Machine", "id": mid, "model": r["model"], "rev": int(r["rev"]), "ship_date": r["ship_date"],
                                       "ship_year": int(r["ship_date"][:4]), "tenant": r["tenant"]}
            nodes.setdefault(("Revision", rid), {"label": "Revision", "id": rid, "model": r["model"], "num": int(r["rev"])})
            edges[(("Machine", mid), "INSTANCE_OF", ("Revision", rid))] = {"s": ["Machine", mid], "rel": "INSTANCE_OF", "o": ["Revision", rid], "props": {"chunk": f"REG:{mid}"}}
    data = {"nodes": list(nodes.values()), "edges": list(edges.values())}
    (DATA / "graph.json").write_text(json.dumps(data))
    return data


class MemGraph:
    def __init__(self, data):
        self.g = nx.MultiDiGraph()
        for n in data["nodes"]: self.g.add_node((n["label"], n["id"]), **n)
        for e in data["edges"]:
            self.g.add_edge(tuple(e["s"]), tuple(e["o"]), key=e["rel"], rel=e["rel"], **e["props"])

    def node(self, label, id_): return self.g.nodes.get((label, id_))
    def exists(self, label, id_): return (label, id_) in self.g

    def _vis(self, n, models):
        return models is None or any(m in models for m in n.get("models", []))

    def doc(self, doc_id): return self.node("Document", doc_id)

    def supersession_chain(self, part):
        hops, cur = [], ("Part", part)
        while True:
            nxt = [(v, d) for _, v, k, d in self.g.out_edges(cur, keys=True, data=True) if k == "SUPERSEDED_BY"]
            if not nxt: return hops
            v, d = nxt[0]; hops.append({"a": cur[1], "b": v[1], "chunk": d["chunk"], "ecn": d.get("ecn"), "effective": d.get("effective")}); cur = v

    def ecn_affects(self, ecn, models=None):
        out = []
        for _, v, k, d in self.g.out_edges(("ECN", ecn), keys=True, data=True):
            if k != "AFFECTS": continue
            n = self.g.nodes[v]
            out.append({"label": v[0], "id": v[1], "chunk": d["chunk"], "visible": v[0] != "Document" or self._vis(n, models)})
        return out

    def machines_using(self, part, y0, y1, tenant="ADMIN"):
        out = []
        for rev, _, k, d1 in self.g.in_edges(("Part", part), keys=True, data=True):
            if k != "USES_PART": continue
            for mach, _, k2, _ in self.g.in_edges(rev, keys=True, data=True):
                if k2 != "INSTANCE_OF": continue
                m = self.g.nodes[mach]
                if y0 <= m["ship_year"] <= y1 and (tenant == "ADMIN" or m["tenant"] == tenant):
                    out.append({"id": m["id"], "model": m["model"], "rev": m["rev"], "year": m["ship_year"], "ship": m["ship_date"],
                                "use_chunk": d1["chunk"], "reg_chunk": f"REG:{m['id']}"})
        return sorted(out, key=lambda x: x["id"])

    def standards(self, model):
        return [{"standard": v[1], "chunk": d["chunk"]} for _, v, k, d in self.g.out_edges(("Model", model), keys=True, data=True) if k == "COMPLIES_WITH"]

    def models(self): return sorted(n[1] for n in self.g.nodes if n[0] == "Model")
    def revisions(self, model): return sorted(self.g.nodes[n]["num"] for n in self.g.nodes if n[0] == "Revision" and self.g.nodes[n].get("model") == model and "effective" in self.g.nodes[n])


class Neo4jGraph:
    """Same interface; every query is a fixed, parameterized, read-only template that passes agents.guard.check first."""
    def __init__(self):
        from neo4j import GraphDatabase
        self.drv = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_RO_USER, NEO4J_RO_PASSWORD))

    def _q(self, cypher, **params):
        from agents.guard import check
        check(cypher)
        with self.drv.session(default_access_mode="READ") as s:
            return [r.data() for r in s.run(cypher, params, timeout=10)]

    def exists(self, label, id_):
        assert label in NODE_LABELS
        return self._q(f"MATCH (n:{label} {{id:$id}}) RETURN count(n) AS c LIMIT 1", id=id_)[0]["c"] > 0

    def supersession_chain(self, part):
        r = self._q("MATCH p=(a:Part {id:$id})-[:SUPERSEDED_BY*1..6]->(b:Part) WITH p ORDER BY length(p) DESC LIMIT 1 "
                    "RETURN [r IN relationships(p) | {a: startNode(r).id, b: endNode(r).id, chunk: r.chunk, ecn: r.ecn, effective: r.effective}] AS hops", id=part)
        return r[0]["hops"] if r else []

    def ecn_affects(self, ecn, models=None):
        rows = self._q("MATCH (e:ECN {id:$id})-[r:AFFECTS]->(t) RETURN labels(t)[0] AS label, t.id AS id, r.chunk AS chunk, t.models AS models LIMIT 200", id=ecn)
        return [{"label": r["label"], "id": r["id"], "chunk": r["chunk"],
                 "visible": r["label"] != "Document" or models is None or any(m in models for m in (r["models"] or []))} for r in rows]

    def machines_using(self, part, y0, y1, tenant="ADMIN"):
        rows = self._q("MATCH (m:Machine)-[:INSTANCE_OF]->(rv:Revision)-[u:USES_PART]->(p:Part {id:$id}) "
                       "WHERE m.ship_year >= $y0 AND m.ship_year <= $y1 AND ($all OR m.tenant = $tenant) "
                       "RETURN m.id AS id, m.model AS model, m.rev AS rev, m.ship_year AS year, m.ship_date AS ship, u.chunk AS use_chunk ORDER BY m.id LIMIT 200",
                       id=part, y0=y0, y1=y1, all=(tenant == "ADMIN"), tenant=tenant)
        return [{**r, "reg_chunk": f"REG:{r['id']}"} for r in rows]

    def standards(self, model):
        return self._q("MATCH (:Model {id:$id})-[r:COMPLIES_WITH]->(s:Standard) RETURN s.id AS standard, r.chunk AS chunk LIMIT 50", id=model)

    def models(self): return [r["id"] for r in self._q("MATCH (m:Model) RETURN m.id AS id ORDER BY id LIMIT 500")]
    def revisions(self, model): return [r["n"] for r in self._q("MATCH (r:Revision {model:$m}) WHERE r.effective IS NOT NULL RETURN r.num AS n ORDER BY n LIMIT 50", m=model)]
    def doc(self, doc_id):
        r = self._q("MATCH (d:Document {id:$id}) RETURN d.models AS models, d.type AS type LIMIT 1", id=doc_id); return r[0] if r else None


def load_neo4j(data):
    """Idempotent MERGE load (admin credentials). Label/rel names come from fixed allowlists, values are parameters."""
    from neo4j import GraphDatabase
    drv = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with drv.session() as s:
        for lab in NODE_LABELS: s.run(f"CREATE CONSTRAINT {lab.lower()}_id IF NOT EXISTS FOR (n:{lab}) REQUIRE n.id IS UNIQUE")
        by = {}
        for n in data["nodes"]: by.setdefault(n["label"], []).append({"id": n["id"], "props": {k: v for k, v in n.items() if k not in ("label", "id")}})
        for lab, rows in by.items():
            assert lab in NODE_LABELS
            s.run(f"UNWIND $rows AS r MERGE (n:{lab} {{id:r.id}}) SET n += r.props", rows=rows)
        grp = {}
        for e in data["edges"]: grp.setdefault((e["s"][0], e["rel"], e["o"][0]), []).append({"s": e["s"][1], "o": e["o"][1], "props": e["props"]})
        for (sl, rel, ol), rows in grp.items():
            assert sl in NODE_LABELS and ol in NODE_LABELS and rel in REL_TYPES
            s.run(f"UNWIND $rows AS r MATCH (a:{sl} {{id:r.s}}) MATCH (b:{ol} {{id:r.o}}) MERGE (a)-[x:{rel}]->(b) SET x += r.props", rows=rows)
    drv.close()


def open_graph():
    if GRAPH_BACKEND == "neo4j": return Neo4jGraph()
    return MemGraph(json.loads((DATA / "graph.json").read_text()))
