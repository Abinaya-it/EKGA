"""FR-4a: fine-tune a local embedding model on in-domain triplets (MultipleNegativesRankingLoss with hard negatives).
BASE_EMB=/models/bge-base-en-v1.5 python -m finetune.train_embedding   ->  models/ekga-bge (point EMB_PATH at it, re-run ingest, compare Recall@10)"""
import json, os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import DATA
from sentence_transformers import SentenceTransformer, InputExample, losses
from torch.utils.data import DataLoader

model = SentenceTransformer(os.environ["BASE_EMB"])
ex = [InputExample(texts=[t["query"], t["positive"], t["negative"]]) for t in map(json.loads, open(DATA / "finetune" / "triplets.jsonl"))]
loader = DataLoader(ex, shuffle=True, batch_size=int(os.getenv("BATCH", 16)))
model.fit(train_objectives=[(loader, losses.MultipleNegativesRankingLoss(model))], epochs=int(os.getenv("EPOCHS", 3)),
          warmup_steps=int(0.1 * len(loader)), output_path=os.getenv("OUT", "models/ekga-bge"), show_progress_bar=True)
