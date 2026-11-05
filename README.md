# EKGA – Engineering Knowledge Graph & Agentic Query Assistant

GraphRAG over machinery manuals/ECNs/bulletins: knowledge graph + hybrid retrieval + agent planner, verifiable citations,
refusals, tenant entitlements. **Runs fully offline** – no Hugging Face / GitHub / cloud needed for the core system.

## Quick start (no models required)
```bash
pip install -r requirements.txt        # offline: pip download on an approved machine -> wheels/, then pip install --no-index --find-links wheels/
make all                               # synthetic corpus (77 docs) + 200-Q gold set -> ingest -> eval baseline vs GraphRAG -> red-team
make test                              # 22 tests (guard, ER, tables, revision-awareness, conflicts, memory, injection, red-team, incremental update)
make serve                             # http://localhost:8000  (keys: key-admin, key-t1 … key-t5)
```
Without an LLM the engine is deterministic (rules + retrieval + verified composition), which makes evaluation reproducible.
Add models to enable the model-based paths (all local):
```bash
export EMB_PATH=/models/bge-base-en-v1.5          # sentence-transformers folder -> dense retrieval (else TF-IDF)
export LLM_URL=http://localhost:11434/v1 LLM_MODEL=qwen2.5:7b-instruct   # Ollama/vLLM: router fallback, LLM-judge, text->Cypher, baseline
export GRAPH_BACKEND=neo4j NEO4J_URI=bolt://localhost:7687               # Neo4j instead of in-memory graph
export VERIFY_LLM=1                                # extra LLM-judge pass in the verifier
```
Getting models/packages past restrictions: ask IT for an internal mirror (Artifactory/Nexus) first; otherwise download on an approved machine
(`huggingface-cli download BAAI/bge-base-en-v1.5 --local-dir …`, `ollama pull`/GGUF) and transfer per your policy. Code loads only from local paths.

## Layout → requirements
| Path | Covers |
|---|---|
| `world/` | Stage 0: ground-truth world, PDF/DOCX rendering (variant spellings, conflicts, injection doc), 200-Q gold set (120/50/30; 100 sealed) |
| `ingest/` | FR-1: layout-aware parse, table-safe chunking, metadata, extraction with chunk provenance; full + incremental update |
| `graph/` | FR-2: 11 node types / 8+ relations, entity resolution with measured precision, memory + Neo4j backends |
| `retrieval/` | vector + BM25 + RRF, **pre-filtered** by entitlement/model/revision |
| `agents/` | FR-3/5: router, planner/handlers, safe-Cypher guard, verification agent, session memory, refusal, injection quarantine, cache |
| `api/` | FR-6: streaming `/chat` (SSE), PDF-page citation click-through, `/feedback`, degraded mode, UI |
| `eval/` | headless harness, sealed-set CI gate, entitlement red-team, perf/concurrency |
| `finetune/` | FR-4: triplets + Recall@10 eval, embedding fine-tune, LoRA text→Cypher |
| `deploy/`, `Dockerfile`, `.github/` | compose (Neo4j+Ollama+app), K8s manifests/HPA, eval-gated CI |
| `docs/` | ontology ADR, runbook |

## Measured results (this repo, seed 7, synthetic corpus, no LLM)
| Metric | Baseline (vector RAG) | GraphRAG | Target |
|---|---|---|---|
| Correctness (200 Q) | 17.5% | 100% | ≥90% |
| Multi-hop (50 Q) | 0% | 100% | ≥80% |
| Citation precision | 56.5% | 100% | ≥95% |
| Refusal correctness | 6.7% | 100% | ≥95% |
| P95 latency | 7 ms | 17 ms | ≤3 s / ≤25 s |
| Cross-tenant leaks (500 attacks) | – | 0 | 0 |
| ER merge precision / recall | | 1.00 / 1.00 | report |

**Read these honestly.** The corpus is clean, template-generated and the engine's handlers match its question shapes, so 100% shows the
*pipeline works end to end*, not real-world accuracy. For the interview and for real data: (1) swap in messier documents (scans → your OCR/hOCR
pipeline), (2) let the LLM do extraction/routing (`llm_extract`, router fallback) and re-measure, (3) add harder gold questions, (4) report
triple precision on a human-labelled sample. The verifier's removal rate is 0% here because composed claims are derived from evidence; `test_verifier_removes_unsupported_claim`
shows it catches tampered claims.

## Not run in this sandbox (written, needs your environment)
Neo4j backend + Cypher agent, K8s/compose deploy, embedding/LoRA training, LLM-judge, Recall@10 with a real embedding model, DR drill, OpenTelemetry exporter
(traces are logged to `data/traces.jsonl`). Cost-reduction work (≥35%) only becomes measurable once LLM calls exist: levers are already wired
(semantic cache, rules-first routing, small-model routing via `LLM_MODEL`, evidence pre-filtering, k limits).

## Interview assets
Architecture = diagram of README layout + `docs/ontology.md`; money slide = the table above on your real data; war story = ER (`Gear Box` vs `Gearbox` was split until the
ordered/unordered dual score was added – see `graph/resolve.py` and `er_report.json`).
