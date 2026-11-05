"""Central config. Everything is overridable by env vars so the same code runs offline/on-prem."""
import os, pathlib

ROOT = pathlib.Path(__file__).parent
DATA = pathlib.Path(os.getenv("EKGA_DATA", ROOT / "data"))
DOCS = DATA / "docs"            # generated PDFs / DOCX
SOURCE = DATA / "source"        # machines.csv, tenants.json
STAGING = DATA / "staging"      # per-document extracted nodes/edges (idempotent rebuild source)

LLM_URL = os.getenv("LLM_URL")                      # OpenAI-compatible, e.g. http://localhost:11434/v1 (Ollama) or vLLM
LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5:7b-instruct")
EMB_PATH = os.getenv("EMB_PATH")                    # local sentence-transformers folder; unset -> TF-IDF fallback
GRAPH_BACKEND = os.getenv("GRAPH_BACKEND", "memory")  # memory | neo4j
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "ekga-password")
NEO4J_RO_USER = os.getenv("NEO4J_RO_USER", NEO4J_USER)          # use a read-only DB user in production
NEO4J_RO_PASSWORD = os.getenv("NEO4J_RO_PASSWORD", NEO4J_PASSWORD)
VERIFY_LLM = os.getenv("VERIFY_LLM", "0") == "1"    # extra LLM-judge pass in the verifier
TOP_K = 10
for p in (DOCS, SOURCE, STAGING):
    p.mkdir(parents=True, exist_ok=True)
