"""FR-3 safe-Cypher gate: LLM-written (or template) Cypher must pass this linter/allowlist before it touches the database.
Defense in depth: also run with a read-only DB user, READ session mode and a query timeout (see graph/store.py)."""
import re

ALLOWED_LABELS = {"Machine", "Model", "Revision", "Component", "Part", "Document", "Section", "ECN", "Spec", "Procedure", "Standard"}
ALLOWED_RELS = {"HAS_COMPONENT", "USES_PART", "SUPERSEDED_BY", "AFFECTS", "DOCUMENTED_IN", "APPLIES_TO", "REFERENCES", "COMPLIES_WITH",
                "HAS_SPEC", "FOR_COMPONENT", "HAS_SECTION", "INSTANCE_OF"}
FORBIDDEN = re.compile(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|CALL|LOAD|FOREACH|APOC|DBMS|COPY|GRANT|DENY|INDEX|CONSTRAINT|USING|PROFILE|EXPLAIN)\b", re.I)
NODE_LABEL = re.compile(r"\(\s*\w*\s*:\s*([A-Za-z_]+)")
REL_TYPE = re.compile(r"\[\s*\w*\s*:\s*([A-Za-z_|]+)")


class UnsafeCypher(ValueError): pass


def check(cypher: str, require_scope=True):
    q = cypher.strip()
    if not q.upper().startswith(("MATCH", "OPTIONAL MATCH", "WITH", "UNWIND")): raise UnsafeCypher("must start with MATCH/WITH/UNWIND")
    if ";" in q or "//" in q or "/*" in q: raise UnsafeCypher("multiple statements / comments not allowed")
    if re.search(r"['\"`]", q): raise UnsafeCypher("string literals not allowed: pass values as $parameters")
    if (m := FORBIDDEN.search(q)): raise UnsafeCypher(f"forbidden keyword {m.group(0)}")
    for lab in NODE_LABEL.findall(q):
        if lab not in ALLOWED_LABELS: raise UnsafeCypher(f"label {lab} not in allowlist")
    for rel in REL_TYPE.findall(q):
        for r in rel.split("|"):
            if r and r not in ALLOWED_RELS: raise UnsafeCypher(f"relationship {r} not in allowlist")
    if not re.search(r"\bLIMIT\s+\d+\b", q, re.I) and not re.search(r"count\(", q, re.I): raise UnsafeCypher("LIMIT required")
    if require_scope and re.search(r"\(\s*\w*\s*:\s*(Machine)\b", q) and "$tenant" not in q and "$all" not in q:
        raise UnsafeCypher("Machine queries must carry the tenant predicate")
    return True
