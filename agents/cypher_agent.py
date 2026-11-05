"""Optional text->Cypher path for questions the templates don't cover (Neo4j backend + LLM only).
Generated Cypher is linted by guard.check, executed read-only with a timeout; any failure falls back to a refusal."""
import json
from agents import llm
from agents.guard import check, UnsafeCypher, ALLOWED_LABELS, ALLOWED_RELS

SCHEMA = f"Labels: {sorted(ALLOWED_LABELS)}\nRelationships: {sorted(ALLOWED_RELS)}\n" \
         "Key properties: Part.id, ECN.id, Document.id/type/models/effective, Machine.id/model/rev/ship_year/tenant, Revision.id/model/num, Spec.value/unit."
SHOT = "Question: Which parts are affected by ECN-2231?\nCypher: MATCH (e:ECN {id:$ecn})-[:AFFECTS]->(p:Part) RETURN p.id AS id LIMIT 50\nParams: {\"ecn\": \"ECN-2231\"}"


def generate(question):
    out = llm.chat([{"role": "system", "content": "Write ONE read-only Cypher query. Use only the schema. Never inline literals; use $params. Always add LIMIT. "
                     "Return JSON {\"cypher\": str, \"params\": object}.\n" + SCHEMA + "\n" + SHOT},
                    {"role": "user", "content": question}], json_mode=True)
    try: d = json.loads(out); return d["cypher"], d.get("params", {})
    except Exception: return None, {}


def run(question, graph, tenant="ADMIN"):
    cy, params = generate(question)
    if not cy: return None
    try:
        check(cy)
        params = {**params, "all": tenant == "ADMIN", "tenant": tenant}
        return graph._q(cy, **params)
    except (UnsafeCypher, Exception):
        return None
