"""Hybrid retrieval: dense (local sentence-transformers when EMB_PATH is set, else TF-IDF) + BM25, fused with RRF.
Entitlement and revision filters are applied BEFORE scoring (candidate rows are sliced first), never as a post-filter."""
import json, math, pickle, re
from collections import Counter
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from config import DATA, EMB_PATH

TOK = re.compile(r"[a-z0-9]+")
tok = lambda s: TOK.findall(s.lower())


class Index:
    def __init__(self, chunks):
        self.chunks = chunks; self.build()

    # ---------------- build
    def build(self):
        texts = [c["text"] for c in self.chunks]
        if EMB_PATH:
            from sentence_transformers import SentenceTransformer
            self.st = SentenceTransformer(EMB_PATH); self.mode = "st"
            self.X = self.st.encode(texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False)
        else:
            self.vec = TfidfVectorizer(token_pattern=r"(?u)\b\w+\b", ngram_range=(1, 2), sublinear_tf=True)
            self.X = self.vec.fit_transform(texts); self.mode = "tfidf"
        self.tf = [Counter(tok(t)) for t in texts]
        self.dl = np.array([sum(c.values()) for c in self.tf], dtype=float)
        self.df = Counter(w for c in self.tf for w in c)

    def __getstate__(self):
        s = self.__dict__.copy(); s.pop("st", None); return s

    def __setstate__(self, s):
        self.__dict__.update(s)
        if s.get("mode") == "st":
            from sentence_transformers import SentenceTransformer
            self.st = SentenceTransformer(EMB_PATH)

    # ---------------- filters (pre-filter)
    def mask(self, allowed_models=None, model=None, types=None, rev=None, doc_ids=None):
        idx = []
        for i, c in enumerate(self.chunks):
            if allowed_models is not None and not any(m in allowed_models for m in c["models"]): continue
            if model and model not in c["models"]: continue
            if types and c["doc_type"] not in types: continue
            if rev is not None and c["rev"] not in (str(rev), "all"): continue
            if doc_ids is not None and c["doc_id"] not in doc_ids: continue
            idx.append(i)
        return np.array(idx, dtype=int)

    # ---------------- scoring on the candidate slice only
    def dense(self, q, idx, k):
        if len(idx) == 0: return []
        if self.mode == "st":
            s = self.X[idx] @ self.st.encode([q], normalize_embeddings=True)[0]
        else:
            s = (self.X[idx] @ self.vec.transform([q]).T).toarray().ravel()
        order = np.argsort(-s)[:k]
        return [(int(idx[j]), float(s[j])) for j in order if s[j] > 0]

    def bm25(self, q, idx, k, k1=1.5, b=0.75):
        if len(idx) == 0: return []
        N = len(self.chunks); avg = self.dl.mean(); qs = tok(q); sc = np.zeros(len(idx))
        for t in set(qs):
            idf = math.log(1 + (N - self.df.get(t, 0) + .5) / (self.df.get(t, 0) + .5))
            for j, i in enumerate(idx):
                f = self.tf[i].get(t, 0)
                if f: sc[j] += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * self.dl[i] / avg))
        order = np.argsort(-sc)[:k]
        return [(int(idx[j]), float(sc[j])) for j in order if sc[j] > 0]

    def hybrid(self, q, idx, k=10, rrf=60):
        fused = Counter()
        for lst in (self.dense(q, idx, 3 * k), self.bm25(q, idx, 3 * k)):
            for rank, (i, _) in enumerate(lst): fused[i] += 1 / (rrf + rank + 1)
        return [i for i, _ in fused.most_common(k)]

    def vector_only(self, q, idx, k=5): return [i for i, _ in self.dense(q, idx, k)]


def save(index): (DATA / "index.pkl").write_bytes(pickle.dumps(index))
def load(): return pickle.loads((DATA / "index.pkl").read_bytes())
def load_chunks(): return [json.loads(l) for l in open(DATA / "chunks.jsonl")]
