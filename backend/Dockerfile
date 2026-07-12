FROM node:20-slim AS frontend-build

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json /frontend/
RUN npm ci --no-audit --no-fund

COPY frontend/ /frontend/
RUN npm run build


FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN python -m pip install --no-cache-dir --upgrade pip

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt

RUN mkdir -p /app/data /app/web

COPY backend/app /app/app
COPY backend/alembic.ini /app/alembic.ini
COPY backend/alembic /app/alembic
# Admin CF deploy (POST /admin/api/cf-workers/deploy) loads hardened Worker sources from edge/.
# Only src modules are required (index.js + pure.js); keep image small.
COPY edge/api-worker/src /app/edge/api-worker/src
COPY edge/img-worker/src /app/edge/img-worker/src
COPY --from=frontend-build /frontend/dist /app/web/dist

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
