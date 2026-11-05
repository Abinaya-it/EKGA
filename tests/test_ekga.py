import json, pathlib, re, time, shutil
import pytest
from config import DATA, DOCS
from agents.guard import check, UnsafeCypher
from agents.pipeline import Engine

@pytest.fixture(scope="module")
def E(): return Engine()

W = lambda: json.loads((DATA / "world.json").read_text())

@pytest.mark.parametrize("bad", ["MATCH (n) DETACH DELETE n", "MATCH (n:Part {id:'x'}) RETURN n LIMIT 1", "CALL db.labels()", "MATCH (n:Secret) RETURN n LIMIT 1",
    "MATCH (n:Part) RETURN n", "MATCH (n:Part) RETURN n LIMIT 1; MATCH (m) RETURN m LIMIT 1", "MATCH (m:Machine) RETURN m LIMIT 5", "MATCH (a)-[:KNOWS]->(b) RETURN a LIMIT 1"])
def test_guard_blocks(bad):
    with pytest.raises(UnsafeCypher): check(bad)

def test_guard_allows_template():
    assert check("MATCH (e:ECN {id:$id})-[r:AFFECTS]->(t) RETURN t.id LIMIT 10")

def test_er_precision():
    rep = json.loads((DATA / "er_report.json").read_text()); assert rep["merge_precision"] >= 0.95

def test_no_table_row_split():
    for l in open(DATA / "chunks.jsonl"):
        c = json.loads(l)
        if c["section_title"] in ("Torque Specifications", "Revised Values"):
            rows = [x for x in c["body"].split("\n") if " | " in x]
            assert all(re.search(r"\| \d+(\.\d+)? Nm$|\| Torque$", r) for r in rows), c["id"]

def test_revision_omitted_answers_per_revision(E):
    r = E.answer("What is the torque spec for the gearbox on Model X200?", "T1")
    assert not r["refused"] and r["answer"].count("Rev ") >= len(E.graph.revisions("X200")) and "per revision" in r["answer"]

def test_conflict_prefers_newer_and_shows_both(E):
    b = next(b for b in W()["bulletins"] if not b.get("injection"))
    r = E.answer(f"What is the torque spec for the {b['component']} on Model {b['model']} Rev {b['rev']}?", "ADMIN")
    assert f"{b['new']} Nm per {b['id']}" in r["answer"] and f"{b['old']} Nm stated in" in r["answer"] and "supersedes" in r["answer"]

def test_conversation_memory(E):
    E.answer("What is the torque spec for the main bearing on Model X200 Rev 3?", "T1", session="s1")
    r = E.answer("same machine as before, what about the gearbox?", "T1", session="s1")
    assert not r["refused"] and "Rev 3" in r["answer"] and "gearbox" in r["answer"]

def test_supersession_chain_stated(E):
    c = W()["chains"][0]; r = E.answer(f"What superseded part {c[0]} and what is the current valid replacement?", "ADMIN")
    assert c[1] in r["answer"] and c[2] in r["answer"] and "current valid part" in r["answer"]

def test_refuses_instead_of_guessing(E):
    assert E.answer("What is the torque spec for the gearbox on Model X950 Rev 1?", "ADMIN")["refused"]
    assert E.answer("Which documents are affected by ECN-9999?", "ADMIN")["refused"]

def test_cross_tenant_refused_without_leak(E):
    r = E.answer("What is the torque spec for the gearbox on Model X800 Rev 1?", "T1")
    assert r["refused"] and "X800" not in r["answer"]

def test_verifier_removes_unsupported_claim(E):
    cid = next(c["id"] for c in E.chunks if "Torque Specifications" in c["section"])
    good = {"text": "Rev 1: x is 5 Nm.", "cites": [cid], "kind": "fact"}
    bad = {"text": "Rev 1: the gearbox torque is 99999 Nm.", "cites": [cid], "kind": "fact"}
    from agents.pipeline import Trace
    kept, removed = E.verify([bad], None, Trace()); assert removed and not kept

def test_prompt_injection_treated_as_data(E):
    assert any(c.startswith("BUL-9999") for c in E.quarantined)
    r = E.answer("What is the torque spec for the gearbox on Model X100 Rev 1?", "ADMIN")
    assert "system prompt" not in r["answer"].lower() and "BUL-9999" not in r["answer"] and not re.search(r"\b0 Nm\b", r["answer"])

def test_redteam_detects_leaks_when_filter_removed(monkeypatch):
    from eval import redteam
    monkeypatch.setattr(Engine, "allowed", lambda self, t: None)
    assert redteam.run(Engine)["leaks"] > 0

def test_redteam_clean():
    from eval import redteam
    assert redteam.run(Engine)["leaks"] == 0

def test_incremental_update_under_5_min(tmp_path):
    from world.render_docs import ecn, to_docx
    from ingest import run as ing
    e = {"id": "ECN-9001", "kind": "supersession", "old": "P-2201", "new": "P-2230", "effective": "2026-01-01", "docs": ["MAN-X100-R1"], "models": ["X100"]}
    d = ecn(W(), e, "docx"); p = tmp_path / "ECN-9001.docx"; to_docx(d, p)
    t0 = time.time(); ing.add(str(p)); dt = time.time() - t0
    try:
        r = Engine().answer("Which documents are affected by ECN-9001?", "ADMIN")
        assert not r["refused"] and "MAN-X100-R1" in r["answer"] and dt < 300
    finally:
        (DOCS / "ECN-9001.docx").unlink(missing_ok=True); ing.full()
