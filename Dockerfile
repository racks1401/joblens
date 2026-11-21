# One Dockerfile, two targets: `api` (FastAPI + ingestion + evals) and `ui` (Streamlit).
# `api` is the last stage, so hosts that build without a target (Render) get the API.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

FROM base AS ui
COPY ui/requirements.txt ui/requirements.txt
RUN pip install -r ui/requirements.txt
COPY ui ui
COPY .streamlit .streamlit
EXPOSE 8501
CMD ["streamlit", "run", "ui/streamlit_app.py", "--server.port", "8501", "--server.address", "0.0.0.0", "--server.headless", "true", "--server.maxUploadSize", "5"]

FROM base AS api
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY alembic.ini pyproject.toml ./
COPY app app
COPY config config
COPY migrations migrations
COPY ml ml
COPY evals evals
COPY scripts scripts
COPY models models
ENV FASTEMBED_CACHE=/app/models/fastembed
EXPOSE 8000
# PORT is set by hosts such as Render (default 10000 there); 8000 locally and in Docker Compose.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
