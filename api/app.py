"""FR-6: streaming chat API, citation click-through (PDF page), feedback capture, health/metrics. Run: uvicorn api.app:app --port 8000"""
import json, time, pathlib, asyncio
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from pydantic import BaseModel
from config import DATA, DOCS
from agents.pipeline import Engine, Baseline

app = FastAPI(title="EKGA")
STATE = {}
def engine():
    if "e" not in STATE: STATE["e"] = Engine()
    return STATE["e"]
def fallback():
    if "b" not in STATE: STATE["b"] = Baseline()
    return STATE["b"]

def tenant_of(key):
    for t, v in engine().tenants.items():
        if v["api_key"] == key: return t
    raise HTTPException(401, "invalid API key")

class Chat(BaseModel):
    question: str
    session_id: str | None = None

class Feedback(BaseModel):
    question: str; answer: str; rating: int; reason: str = ""

def sse(event, data): return f"event: {event}\ndata: {json.dumps(data)}\n\n"

@app.post("/chat")
async def chat(body: Chat, x_api_key: str = Header(None)):
    tenant = tenant_of(x_api_key); degraded = False
    try: r = engine().answer(body.question, tenant, body.session_id)
    except PermissionError: raise HTTPException(403, "forbidden")
    except Exception:                                   # graph/DB down -> read-only vector-only mode + banner
        r = fallback().answer(body.question, tenant, body.session_id); degraded = True
    with open(DATA / "traces.jsonl", "a") as f: f.write(json.dumps({"ts": time.time(), "tenant": tenant, "q": body.question, "route": r["route"], "refused": r["refused"], "latency_ms": r["latency_ms"], "trace": r["trace"], "llm_calls": r["llm_calls"]}) + "\n")
    async def gen():
        if degraded: yield sse("warning", "Knowledge graph unavailable: vector-only answers, multi-hop questions may be incomplete.")
        yield sse("plan", {"route": r["route"], "trace": r["trace"]})
        for word in r["answer"].split(" "):
            yield sse("token", word + " "); await asyncio.sleep(0)
        yield sse("citations", r["citations"])
        yield sse("done", {"refused": r["refused"], "latency_ms": r["latency_ms"], "removed_claims": r["removed_claims"]})
    return StreamingResponse(gen(), media_type="text/event-stream")

@app.get("/doc/{doc_id}")
def doc(doc_id: str, key: str = Query(...)):
    tenant = tenant_of(key); allowed = engine().allowed(tenant)
    d = engine().graph.doc(doc_id)
    if not d or (allowed is not None and not any(m in allowed for m in d["models"])): raise HTTPException(404)
    f = next(DOCS.glob(f"{doc_id}.*"))
    return FileResponse(f, media_type="application/pdf" if f.suffix == ".pdf" else "application/octet-stream", filename=None if f.suffix == ".pdf" else f.name)

@app.post("/feedback")
def feedback(fb: Feedback, x_api_key: str = Header(None)):
    tenant = tenant_of(x_api_key)
    with open(DATA / "feedback.jsonl", "a") as f: f.write(json.dumps({"ts": time.time(), "tenant": tenant, **fb.model_dump()}) + "\n")
    return {"ok": True}

@app.get("/health")
def health(): return {"ok": True, "graph_backend": type(engine().graph).__name__}

@app.get("/metrics")
def metrics(): return engine().stats

@app.get("/", response_class=HTMLResponse)
def ui(): return (pathlib.Path(__file__).parent / "static" / "index.html").read_text()
