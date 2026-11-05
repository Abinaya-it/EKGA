# Runbook

## Rebuild / update
- Full rebuild (idempotent): `python -m ingest.run` · Single new ECN/bulletin: `python -m ingest.run --add path/to/ECN-xxxx.pdf` (prints elapsed; target < 5 min, measured in seconds).
- Neo4j: loads with `MERGE`, safe to re-run. Backup: `neo4j-admin database dump neo4j --to-path=/backups`; restore: `neo4j-admin database load neo4j --from-path=/backups --overwrite-destination` (drill target RTO ≤ 30 min – time it and record here).

## Degraded mode
If the graph backend raises, `/chat` falls back to the vector-only baseline and emits an SSE `warning` event (UI shows a banner). Alert: `GRAPH_DOWN`.

## Incidents
| Symptom | Check | Action |
|---|---|---|
| Embedding/LLM endpoint down | `/health`, `traces.jsonl` llm_calls errors | System is LLM-optional: router/verify fall back to deterministic rules; restart Ollama/vLLM; set `LLM_URL` empty to force rules-only |
| Refusal rate spike | `data/traces.jsonl` (`refused`), eval_report false_refusal_rate | Usually ingestion gap: run `ingest.run`; check `er_report.json`; look for new component spellings |
| Verifier removing many claims | trace step `verify.removed` | Parsing regression or a changed document layout; diff `chunks.jsonl` |
| Suspected cross-tenant leak | `python -m eval.redteam` | Treat as Sev-1; disable route, inspect `allowed()` and graph predicates |
| Prompt injection in new doc | `Engine.quarantined` | Chunk is data-only: never inserted into LLM prompts; deterministic composer ignores instructions |

## Alerts to configure (OpenTelemetry/Prometheus on `/metrics` + `traces.jsonl`)
hallucination proxy (verifier removed/claims), P95 latency SLO burn, cost/query (llm_calls × token price), index staleness (newest doc mtime vs index.pkl), refusal/feedback score drift.
