FROM python:3.12-slim
WORKDIR /app
COPY wheels/ /wheels/
COPY requirements.txt .
# offline-friendly: prefer the local wheelhouse (pip download -r requirements.txt -d wheels/), fall back to the internal index
RUN pip install --no-cache-dir --find-links /wheels -r requirements.txt || pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python world/generate_world.py && python world/render_docs.py && python world/make_gold.py && python -m ingest.run
EXPOSE 8000
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
