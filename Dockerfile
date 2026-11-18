# One Dockerfile, two targets: `api` (FastAPI + ingestion + evals) and `ui` (Streamlit).
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

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
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]

FROM base AS ui
COPY requirements-ui.txt .
RUN pip install -r requirements-ui.txt
COPY ui ui
EXPOSE 8501
CMD ["streamlit", "run", "ui/streamlit_app.py", "--server.port", "8501", "--server.address", "0.0.0.0", "--server.headless", "true"]
