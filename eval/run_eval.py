"""Headless evaluation: python -m eval.run_eval [--split dev|sealed|all] [--system graphrag|baseline|both] [--gate eval/last_good.json]
Metrics: answer correctness, multi-hop correctness, citation precision, claim-removal rate, refusal correctness, latency."""
import argparse, json, re, sys, time, pathlib, statistics as st
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import DATA
from agents.pipeline import Engine, Baseline, REFUSAL

NUM = re.compile(r"(\d+(?:\.\d+)?)\s*Nm")


def norm(s): return re.sub(r"\s+", " ", s.lower())


def correct(g, r):
    a = norm(r["answer"])
    if g["refuse"]: return r["refused"]
    if r["refused"]: return False
    if any(norm(x) not in a for x in g["must_contain"]): return False
    if any(norm(x) in a for x in g.get("must_not_contain", [])): return False
    if "first_value" in g:                                    # revision-specific answers must lead with the right value
        m = NUM.search(r["answer"]); return bool(m) and float(m.group(1)) == float(g["first_value"])
    return True


def run(system, gold):
    E = Engine() if system == "graphrag" else Baseline(); res = []
    for g in gold:
        r = E.answer(g["question"], g["tenant"]); ok = correct(g, r)
        cited = [c["doc"] for c in r["citations"]]
        cp = (sum(d in g["gold_docs"] for d in cited) / len(cited)) if cited and not g["refuse"] else None
        res.append({"qid": g["qid"], "type": g["type"], "ok": ok, "cp": cp, "refused": r["refused"], "should_refuse": g["refuse"],
                    "removed": r["removed_claims"], "claims": len(r["claims"]), "ms": r["latency_ms"], "answer": r["answer"][:300], "question": g["question"]})
    return res


def summarize(res):
    pct = lambda xs: round(100 * sum(xs) / len(xs), 1) if xs else None
    by = lambda t: [x["ok"] for x in res if x["type"] == t]
    answerable = [x for x in res if not x["should_refuse"]]
    cps = [x["cp"] for x in res if x["cp"] is not None]
    removed, claims = sum(x["removed"] for x in res), sum(x["claims"] + x["removed"] for x in res)
    lat = sorted(x["ms"] for x in res)
    return {"n": len(res), "correctness_all": pct([x["ok"] for x in res]), "correctness_answerable": pct([x["ok"] for x in answerable]),
            "single_hop": pct(by("single_hop") + by("single_hop_norev")), "multi_hop": pct(by("multi_hop")),
            "refusal_correctness": pct([x["ok"] for x in res if x["should_refuse"]]),
            "false_refusal_rate": pct([x["refused"] for x in answerable]),
            "citation_precision": pct(cps) if cps else None, "claims_removed_by_verifier_pct": round(100 * removed / claims, 2) if claims else 0.0,
            "latency_p50_ms": lat[len(lat) // 2], "latency_p95_ms": lat[int(len(lat) * .95) - 1]}


def gate(summary, ref_path):
    ref = json.loads(pathlib.Path(ref_path).read_text()); bad = []
    if summary["correctness_all"] < ref["correctness_all"] - 2: bad.append(f"correctness {summary['correctness_all']} < {ref['correctness_all']} - 2")
    if (summary["citation_precision"] or 0) < (ref["citation_precision"] or 0): bad.append(f"citation precision dropped {summary['citation_precision']} < {ref['citation_precision']}")
    return bad


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--split", default="all"); ap.add_argument("--system", default="both"); ap.add_argument("--gate"); ap.add_argument("--save-ref")
    a = ap.parse_args()
    gold = [json.loads(l) for l in open(DATA / "gold_qa.jsonl")]
    if a.split != "all": gold = [g for g in gold if g["split"] == a.split]
    out = {}
    for s in (["baseline", "graphrag"] if a.system == "both" else [a.system]):
        res = run(s, gold); out[s] = {"summary": summarize(res), "failures": [x for x in res if not x["ok"]][:25]}
    (DATA / "eval_report.json").write_text(json.dumps(out, indent=1))
    keys = list(next(iter(out.values()))["summary"])
    print(f"{'metric':34}" + "".join(f"{s:>12}" for s in out))
    for k in keys: print(f"{k:34}" + "".join(f"{str(out[s]['summary'][k]):>12}" for s in out))
    if a.save_ref: pathlib.Path(a.save_ref).write_text(json.dumps(out["graphrag"]["summary"], indent=1))
    if a.gate:
        bad = gate(out["graphrag"]["summary"], a.gate)
        if bad: print("GATE FAILED:", *bad, sep="\n  "); sys.exit(1)
        print("GATE PASSED")
