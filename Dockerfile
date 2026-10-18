# One image, two processes: `api` (FastAPI + static frontend) and `worker`.
# When a GPU model is added, switch the runtime base to a CUDA image
# (e.g. nvidia/cuda:12.x-cudnn-runtime) and install the framework there.

FROM node:22-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    RULES_DIR=/app/rules \
    MODELS_DIR=/models \
    FRONTEND_DIR=/app/frontend/out
WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN pip install --no-cache-dir .
COPY backend/start.sh ./start.sh
COPY rules /app/rules
COPY --from=frontend /frontend/out /app/frontend/out
RUN mkdir -p /data /models

EXPOSE 8000
# Default: API + worker in one container (Cloudflare). docker-compose.yml
# overrides the command to run them as two services.
CMD ["./start.sh"]
