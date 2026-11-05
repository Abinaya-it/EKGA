# ADR-001: Ontology and graph design

**Status:** accepted · **Decision owners:** Knowledge Graph Lead

## Nodes (all 11 required types)
Machine · Model · Revision · Component · Part · Document · Section · ECN · Spec · Procedure · Standard

| Node | id | key properties |
|---|---|---|
| Machine | serial (M-00012) | model, rev, ship_date, ship_year, tenant (from installed-base registry) |
| Model / Revision | X200 / X200-R3 | Revision: model, num, effective |
| Component | resolved label ("main bearing") | aliases (all surface forms merged by ER) |
| Part | P-2201 | desc |
| Document | MAN-X200-R3, ECN-2224, BUL-0007, SPC-X200 | type, effective, models[], file |
| Section | `<doc>#<n>` | title, page |
| ECN | ECN-2224 | effective, models |
| Spec | `SPEC:<doc>:<surface>` | value, unit, effective, section, page |
| Procedure | MP-X200-R3-01 | title |
| Standard | "ISO 9001" | |

## Relations (8 required + 4 supporting)
`HAS_COMPONENT`(Model→Component) · `USES_PART`(Revision→Part) · `SUPERSEDED_BY`(Part→Part, Document→Document; props ecn, effective) ·
`AFFECTS`(ECN→Document|Part) · `DOCUMENTED_IN`(Spec|Procedure|ECN→Document) · `APPLIES_TO`(Document→Model|Revision) ·
`REFERENCES`(Document→Document) · `COMPLIES_WITH`(Model→Standard) · supporting: `HAS_SPEC`, `FOR_COMPONENT`, `HAS_SECTION`, `INSTANCE_OF`(Machine→Revision).

Every edge carries `chunk` (the chunk id that evidences it). That is what makes graph answers citable and verifiable.

## Decisions
1. **Provenance on edges, not nodes** – a fact is only as good as its passage; the verifier re-checks claims against `chunk`.
2. **Entities are resolved at load time, not extraction time** – extraction emits `~surface` component ids; `graph/resolve.py` clusters them (order-insensitive + order-preserving fuzzy score, ≥85 auto-merge, 70–85 LLM adjudication, <70 separate). Merge precision/recall are measured against labelled aliases (`data/er_report.json`).
3. **Idempotent rebuild** – staging JSON per document is the source of truth; the graph (memory or Neo4j `MERGE`) is derived. Incremental update = re-stage one document, rerun ER, re-merge its subgraph.
4. **Safe Cypher** – fixed parameterized templates first; LLM-written Cypher only via `agents/guard.py` (read-only keyword denylist, label/rel allowlist, no literals, mandatory LIMIT, tenant predicate required on Machine queries) + read-only DB user + READ session + timeout.
5. **Entitlements are pre-filters** – vector/BM25 candidate rows are sliced by allowed models *before* scoring; graph queries take `models`/`tenant` predicates; a final per-claim check drops any claim whose cited chunk is outside the tenant's entitlements (defence in depth).
6. **Conflict policy** – when two documents give different values for the same (model, revision, component), the newest *effective* document is stated first and the older one is reported explicitly as superseded.
