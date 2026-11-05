"""Stage 0a: build the ground-truth 'world' (machines, parts, ECNs, specs). Documents and gold Q&A are rendered from it."""
import json, random, datetime as dt, argparse, sys, pathlib, csv
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import DATA, SOURCE

COMPONENTS = {  # canonical -> surface variants used in documents (entity-resolution ground truth)
    "main bearing":   ["Main Bearing", "Main Bearing Assembly", "Bearing (Main)", "Main-Bearing", "Main Beering"],
    "gearbox":        ["Gearbox", "Gear Box", "Gearbox Assembly", "Gear-box", "Gearbx"],
    "hydraulic pump": ["Hydraulic Pump", "Hydraulic Pump Unit", "Pump, Hydraulic", "Hydraulic-Pump Assembly", "Hydralic Pump"],
    "drive coupling": ["Drive Coupling", "Drive Coupling Hub", "Coupling, Drive", "Drive-Coupling Assembly", "Drive Coupling"],
    "shaft seal":     ["Shaft Seal", "Shaft Seal Assembly", "Seal, Shaft", "Shaft-Seal", "Shaft Sael"],
    "relief valve":   ["Relief Valve", "Relief Valve Assembly", "Valve, Relief", "Relief-Valve", "Releif Valve"],
}
STANDARDS = ["ISO 12944 C5", "ISO 9001", "IEC 60204-1", "ISO 13849-1", "DNV-ST-0126", "EN 1090-2"]
TENANTS = ["T1", "T2", "T3", "T4", "T5"]
REV_TO_CHAIN_IDX = {1: 0, 2: 0, 3: 1, 4: 2}   # revision -> index into the part-supersession chain


def build(seed=7, n_models=8, n_machines=300):
    rng = random.Random(seed)
    iso = lambda x: x.isoformat()
    models = [f"X{100 * (i + 1)}" for i in range(n_models)]
    chains, no = [], 2201
    for _ in range(4):                      # 4 supersession chains of 3 pump parts, each shared by 2 models
        chain = []
        for _ in range(3):
            chain.append(f"P-{no}"); no += rng.randint(9, 40)
        chains.append(chain)
    comps = list(COMPONENTS)
    W = {"models": {}, "ecns": [], "bulletins": [], "machines": [], "tenants": {}, "chains": chains}
    for mi, m in enumerate(models):
        nrev = rng.choice([3, 4, 4])
        base = dt.date(2010 + rng.randint(0, 3), rng.randint(1, 12), 1)
        revs, prev = {}, {}
        for r in range(1, nrev + 1):
            eff = base + dt.timedelta(days=int(r * rng.randint(600, 800)))
            vals = {}
            for c in comps:
                v = rng.randrange(80, 900, 5)
                while v == prev.get(c):
                    v = rng.randrange(80, 900, 5)
                vals[c] = v
            prev = vals
            revs[r] = {"effective": iso(eff), "torque": vals, "pump_part": chains[mi % 4][REV_TO_CHAIN_IDX[r]],
                       "filler_parts": [f"P-7{mi}0{k}" for k in range(1, 4)]}
        W["models"][m] = {"revisions": revs, "standards": sorted(rng.sample(STANDARDS, 3)), "family": mi % 4}
    ecn_no = 2224
    for fi, chain in enumerate(chains):     # supersession ECNs
        for k in range(2):
            old, new = chain[k], chain[k + 1]
            users = [(m, r) for m, mm in W["models"].items() if mm["family"] == fi
                     for r, rr in mm["revisions"].items() if rr["pump_part"] == old]
            first_new = min(dt.date.fromisoformat(mm["revisions"][r]["effective"]) for m, mm in W["models"].items()
                            if mm["family"] == fi for r in mm["revisions"] if mm["revisions"][r]["pump_part"] == new)
            W["ecns"].append({"id": f"ECN-{ecn_no}", "kind": "supersession", "old": old, "new": new,
                              "effective": iso(first_new - dt.timedelta(days=60)),
                              "docs": [f"MAN-{m}-R{r}" for m, r in users], "models": sorted({m for m, _ in users})})
            ecn_no += rng.randint(1, 9)
    manuals = [f"MAN-{m}-R{r}" for m, mm in W["models"].items() for r in mm["revisions"]]
    for _ in range(14):                     # noise ECNs (label changes etc.)
        docs = rng.sample(manuals, rng.randint(2, 4))
        W["ecns"].append({"id": f"ECN-{ecn_no}", "kind": "label", "docs": docs,
                          "effective": iso(dt.date(2012, 1, 1) + dt.timedelta(days=rng.randint(0, 3800))),
                          "models": sorted({x.split("-")[1] for x in docs})})
        ecn_no += rng.randint(1, 9)
    keys = [(m, r, c) for m, mm in W["models"].items() for r in mm["revisions"] for c in comps]
    for i, (m, r, c) in enumerate(rng.sample(keys, 16), 1):   # bulletins that override manual torque values
        rr = W["models"][m]["revisions"][r]
        W["bulletins"].append({"id": f"BUL-{i:04d}", "model": m, "rev": r, "component": c, "old": rr["torque"][c],
                               "new": rr["torque"][c] + rng.choice([-30, -20, 20, 35, 50]),
                               "effective": iso(dt.date.fromisoformat(rr["effective"]) + dt.timedelta(days=rng.randint(200, 500)))})
    W["bulletins"].append({"id": "BUL-9999", "model": models[0], "rev": 1, "component": "gearbox",
                           "old": None, "new": None, "effective": "2015-01-01", "injection": True})
    for i, t in enumerate(TENANTS):
        W["tenants"][t] = {"models": sorted({models[(2 * i + k) % n_models] for k in range(3)}), "api_key": f"key-{t.lower()}"}
    for i in range(1, n_machines + 1):
        m = rng.choice(models); mm = W["models"][m]; r = rng.choice(list(mm["revisions"]))
        lo = dt.date.fromisoformat(mm["revisions"][r]["effective"])
        hi = dt.date.fromisoformat(mm["revisions"][r + 1]["effective"]) if r + 1 in mm["revisions"] else dt.date(2024, 12, 31)
        ship = lo + dt.timedelta(days=rng.randint(0, max((hi - lo).days - 1, 1)))
        owners = [t for t, v in W["tenants"].items() if m in v["models"]]
        W["machines"].append({"id": f"M-{i:05d}", "model": m, "rev": r, "ship": iso(ship), "tenant": rng.choice(owners)})
    return W


def write_sources(W):
    (DATA / "world.json").write_text(json.dumps(W, indent=1))
    with open(SOURCE / "machines.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["serial", "model", "rev", "ship_date", "tenant"])
        for m in W["machines"]:
            w.writerow([m["id"], m["model"], m["rev"], m["ship"], m["tenant"]])
    tenants = {"ADMIN": {"models": "*", "api_key": "key-admin"}}
    tenants.update({t: {"models": v["models"], "api_key": v["api_key"]} for t, v in W["tenants"].items()})
    (SOURCE / "tenants.json").write_text(json.dumps(tenants, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7); ap.add_argument("--models", type=int, default=8)
    ap.add_argument("--machines", type=int, default=300)
    a = ap.parse_args()
    W = build(a.seed, a.models, a.machines)
    write_sources(W)
    print(f"world: {len(W['models'])} models, {len(W['ecns'])} ECNs, {len(W['bulletins'])} bulletins, {len(W['machines'])} machines")
