# ── Stage 1: Build React frontend ────────────────────────────────────────────
FROM node:22-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
# Build-time env vars (VITE_ prefix makes them available inside React)
ARG VITE_TELEGRAM_FREE_URL=""
ENV VITE_TELEGRAM_FREE_URL=$VITE_TELEGRAM_FREE_URL
RUN npm run build

# ── Stage 2: Python backend ───────────────────────────────────────────────────
FROM python:3.13-slim
WORKDIR /app

# Install Python dependencies
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ ./

# Copy built React app — main.py mounts this at runtime if the directory exists
COPY --from=frontend-build /build/frontend/dist ./frontend_dist

EXPOSE 8000

COPY docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
