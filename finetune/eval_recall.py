"""Recall@10 (a chunk from a gold document is in the top-10) for the dense retriever, no filters. Compare base vs tuned by switching EMB_PATH.
   python -m finetune.eval_recall [--split dev|sealed]"""
import json, sys, argparse, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import DATA, EMB_PATH
from retrieval.index import Index, load_chunks
import numpy as np

ap = argparse.ArgumentParser(); ap.add_argument("--split", default="dev"); a = ap.parse_args()
gold = [g for g in map(json.loads, open(DATA / "gold_qa.jsonl")) if not g["refuse"] and g["split"] == a.split and g["type"] in ("single_hop", "single_hop_norev")]
ix = Index(load_chunks()); idx = np.arange(len(ix.chunks)); hit = 0
for g in gold:
    top = [ix.chunks[i]["doc_id"] for i, _ in ix.dense(g["question"], idx, 10)]
    hit += any(d in g["gold_docs"] for d in top)
print(f"mode={ix.mode} emb={EMB_PATH} split={a.split} Recall@10={100 * hit / len(gold):.1f}% over {len(gold)} questions")
