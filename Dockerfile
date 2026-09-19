FROM node:20-alpine AS frontend-build

WORKDIR /app/frontend
COPY frontend/package.json frontend/yarn.lock ./
RUN corepack enable && yarn install --frozen-lockfile

COPY frontend/ ./
ENV REACT_APP_BACKEND_URL=
RUN yarn build


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY backend/requirements-prod.txt ./backend/requirements-prod.txt
RUN pip install --no-cache-dir -r backend/requirements-prod.txt

COPY backend/ ./backend/
COPY --from=frontend-build /app/frontend/build ./frontend/build

EXPOSE 10000
CMD ["sh", "-c", "uvicorn backend.server:app --host 0.0.0.0 --port ${PORT:-10000}"]
