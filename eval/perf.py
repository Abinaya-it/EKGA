"""Latency / concurrency / cache check: python -m eval.perf  (50 simultaneous sessions, P95 latency, semantic-cache hit rate)."""
import json, sys, pathlib, time, statistics as st
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import DATA
from agents.pipeline import Engine

E = Engine(); gold = [json.loads(l) for l in open(DATA / "gold_qa.jsonl")]
def one(i):
    g = gold[i % len(gold)]; t = time.time(); E.answer(g["question"], g["tenant"]); return (time.time() - t) * 1000
t0 = time.time()
with ThreadPoolExecutor(50) as ex: lat = sorted(ex.map(one, range(400)))
print(f"400 queries / 50 threads: wall {time.time() - t0:.1f}s  p50 {lat[200]:.0f}ms  p95 {lat[379]:.0f}ms  cache hit rate {100 * E.stats['cache_hits'] / E.stats['queries']:.0f}%")
