"""FR-2: entity resolution. Fuzzy-merge component surface forms; gray-zone pairs go to LLM adjudication (if configured)."""
import re, json
from collections import Counter
from rapidfuzz import fuzz
from config import DATA

NOISE = {"assembly", "assy", "unit", "hub", "housing", "the", "of"}
AUTO, GRAY = 85, 70          # >=AUTO merge; GRAY..AUTO -> LLM adjudication; below -> separate entity


def _toks(s): return [t for t in re.findall(r"[a-z]+", s.lower()) if t not in NOISE]
def norm(s): return "".join(sorted(_toks(s)))            # order-insensitive form ("Pump, Hydraulic" == "Hydraulic Pump")
def score(a, b):                                          # also compare the ordered, space-free form ("Gear Box" == "Gearbox")
    return max(fuzz.ratio(norm(a), norm(b)), fuzz.ratio("".join(_toks(a)), "".join(_toks(b))))


def adjudicate(a, b):
    from agents import llm
    out = llm.chat([{"role": "user", "content": f"Are '{a}' and '{b}' the same machine component? Answer yes or no."}], max_tokens=3)
    return None if out is None else out.strip().lower().startswith("y")


def resolve(surfaces):
    """surfaces: iterable of raw names (with repeats). Returns {surface: cluster_label}, clusters {label: [surfaces]}."""
    cnt = Counter(surfaces); clusters = []          # each: {"rep": norm, "members": [surface,...]}
    for s, _ in cnt.most_common():
        best, top = None, 0
        for c in clusters:
            sc = max(score(s, m) for m in c["members"][:3])
            if sc > top: best, top = c, sc
        if best and (top >= AUTO or (top >= GRAY and adjudicate(s, best["members"][0]))):
            best["members"].append(s)
        else:
            clusters.append({"members": [s]})
    mapping, out = {}, {}
    for c in clusters:                               # label = medoid surface (lowest-cost to others), shortest on ties
        ms = c["members"]
        medoid = max(ms, key=lambda x: (sum(score(x, y) for y in ms), -len(x)))
        label = " ".join(w for w in re.findall(r"[a-z]+", medoid.lower()) if w not in NOISE)
        out[label] = ms
        for m in ms: mapping[m] = label
    return mapping, out


def evaluate(mapping):
    """Merge precision/recall against the generator's ground truth (evaluation only; never used at build time)."""
    from world.generate_world import COMPONENTS
    truth = {v.lower(): k for k, vs in COMPONENTS.items() for v in vs}
    surfs = [s for s in mapping if s.lower() in truth]
    tp = fp = fn = 0
    for i, a in enumerate(surfs):
        for b in surfs[i + 1:]:
            same_pred, same_true = mapping[a] == mapping[b], truth[a.lower()] == truth[b.lower()]
            tp += same_pred and same_true; fp += same_pred and not same_true; fn += (not same_pred) and same_true
    p = tp / (tp + fp) if tp + fp else 1.0; r = tp / (tp + fn) if tp + fn else 1.0
    return {"merge_precision": round(p, 4), "merge_recall": round(r, 4), "surfaces": len(surfs), "clusters": len(set(mapping.values())), "true_components": len(set(truth.values()))}


def find_component(query, mapping):
    """Map a component mention in a user question to its cluster label (n-gram fuzzy match on the normalized form)."""
    words = re.findall(r"[a-z]+", query.lower()); best, top = None, 0
    for n in (1, 2, 3):
        for i in range(len(words) - n + 1):
            w = " ".join(words[i:i + n])
            if len("".join(_toks(w))) < 5: continue
            for s, label in mapping.items():
                sc = score(w, s)
                if sc > top: best, top = label, sc
    return best if top >= 88 else None
