# syntax=docker/dockerfile:1.7
# AC-1: context=eval/, ENTRYPOINT=python -m eval.ragas_runner
# AC-2: bakes runner code + locked deps (--frozen) + baseline.json; NOT specs/golden_qa.jsonl
# AC-4: --extra live installs Ragas/OpenAI/datasets without torch/sentence-transformers
# AC-5: all deps installed at build time; no runtime pip/uv install
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir uv

# Deps layer — copy manifests first for cache reuse across code-only changes.
WORKDIR /app/eval
COPY pyproject.toml uv.lock ./

# Install live deps only (no test/lint tools). --no-install-project: skip building the eval
# wheel since source is served via raw COPY below (OQ-2: no editable install needed).
RUN uv sync --frozen --extra live --no-dev --no-install-project

# Bake runner source (eval/*.py) and baseline.json. eval/.dockerignore excludes tests/,
# runs/, fixtures/, __pycache__, seed_test_corpus.py. specs/golden_qa.jsonl is never
# in the build context (context=eval/), satisfying AC-2 golden-set exclusion.
WORKDIR /app
COPY . /app/eval/

# /app is on sys.path so `import eval.ragas_runner` resolves (OQ-2).
# venv bin on PATH so `python` is the uv-synced interpreter (carries live deps), not system python.
ENV PYTHONPATH=/app PATH="/app/eval/.venv/bin:$PATH"
ENTRYPOINT ["python", "-m", "eval.ragas_runner"]
