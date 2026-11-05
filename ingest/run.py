"""Ingestion pipeline. `python -m ingest.run` = full idempotent rebuild; `python -m ingest.run --add FILE` = incremental update."""
import argparse, json, glob, shutil, time, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import DATA, DOCS, STAGING, GRAPH_BACKEND
from ingest.parse import parse, chunk
from ingest.extract import extract_doc, save
from graph.resolve import resolve, evaluate
from graph.store import build_graph_data, load_neo4j
from retrieval import index as ix


def _chunks_for(path):
    p = parse(path); return p["meta"], chunk(p)


def _finish(all_chunks, t0, incremental_doc=None):
    """ER over all staged surfaces -> graph rebuild -> index rebuild. Cheap enough to rerun after a single new document."""
    surfaces = [n["id"][1:] for f in glob.glob(str(STAGING / "*.json")) for n in json.loads(open(f).read())["nodes"]
                if n["label"] == "Component" and n["id"].startswith("~")]
    mapping, clusters = resolve(surfaces)
    (DATA / "resolved_components.json").write_text(json.dumps(mapping, indent=1))
    er = evaluate(mapping); (DATA / "er_report.json").write_text(json.dumps(er, indent=1))
    graph = build_graph_data(mapping)
    if GRAPH_BACKEND == "neo4j":
        if incremental_doc:       # only the new document's subgraph + machines are re-merged
            st = json.loads((STAGING / f"{incremental_doc}.json").read_text())
            sub = {"nodes": st["nodes"], "edges": st["edges"]}
            fix = lambda e: [e[0], mapping.get(e[1][1:], e[1][1:]) if e[0] == "Component" and e[1].startswith("~") else e[1]]
            for n in sub["nodes"]: n["id"] = fix([n["label"], n["id"]])[1]
            for e in sub["edges"]: e["s"], e["o"] = fix(e["s"]), fix(e["o"])
            load_neo4j(sub)
        else:
            load_neo4j(graph)
    ix.save(ix.Index(all_chunks))
    print(f"graph: {len(graph['nodes'])} nodes / {len(graph['edges'])} edges | ER: {er} | {time.time() - t0:.1f}s")


def full():
    t0 = time.time()
    shutil.rmtree(STAGING, ignore_errors=True); STAGING.mkdir(parents=True)
    all_chunks, metas = [], {}
    for f in sorted(glob.glob(str(DOCS / "*.*"))):
        meta, chunks = _chunks_for(f)
        all_chunks += chunks; metas[meta["id"]] = meta; save(extract_doc(meta, chunks))
    with open(DATA / "chunks.jsonl", "w") as fh:
        for c in all_chunks: fh.write(json.dumps(c) + "\n")
    (DATA / "docs_meta.json").write_text(json.dumps(metas, indent=1))
    print(f"parsed {len(metas)} docs -> {len(all_chunks)} chunks")
    _finish(all_chunks, t0)


def add(path):
    """Incremental: replace one document's chunks + staging, then refresh graph/index (target: queryable in < 5 min)."""
    t0 = time.time()
    meta, chunks = _chunks_for(path)
    dst = DOCS / pathlib.Path(path).name
    if pathlib.Path(path).resolve() != dst.resolve(): shutil.copy(path, dst)
    old = ix.load_chunks(); all_chunks = [c for c in old if c["doc_id"] != meta["id"]] + chunks
    save(extract_doc(meta, chunks))
    with open(DATA / "chunks.jsonl", "w") as fh:
        for c in all_chunks: fh.write(json.dumps(c) + "\n")
    metas = json.loads((DATA / "docs_meta.json").read_text()); metas[meta["id"]] = meta
    (DATA / "docs_meta.json").write_text(json.dumps(metas, indent=1))
    _finish(all_chunks, t0, incremental_doc=meta["id"])
    print(f"incremental update of {meta['id']} took {time.time() - t0:.2f}s")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--add")
    a = ap.parse_args()
    add(a.add) if a.add else full()
