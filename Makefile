PY=python3
.PHONY: all data ingest eval redteam test ci serve clean
all: data ingest eval redteam                 ## full pipeline: generate corpus -> ingest -> evaluate -> red-team
data:                                         ## synthetic corpus + gold set + sources (deterministic; --seed to vary)
	$(PY) world/generate_world.py && $(PY) world/render_docs.py && $(PY) world/make_gold.py
ingest:                                       ## idempotent full rebuild: parse -> extract -> ER -> graph -> index
	$(PY) -m ingest.run
eval:
	$(PY) -m eval.run_eval --split all
redteam:
	$(PY) -m eval.redteam
test:
	$(PY) -m pytest -q
ci: data ingest test                          ## merge gate: tests + sealed-set regression gate (>2pt correctness drop or ANY citation-precision drop fails)
	$(PY) -m eval.run_eval --system graphrag --split sealed --gate eval/last_good.json
serve:
	uvicorn api.app:app --host 0.0.0.0 --port 8000
clean:
	rm -rf data/docs data/staging data/*.json data/*.jsonl data/*.pkl data/source data/finetune
