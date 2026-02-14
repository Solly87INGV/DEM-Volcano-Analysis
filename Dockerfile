# =========================
# 1) Build frontend (React)
# =========================
FROM node:20-bookworm AS frontend_build
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# =========================
# 2) Runtime: Node + Python
# =========================
FROM node:20-bookworm AS runtime
WORKDIR /app

# System deps (robusti per matplotlib/PyQt5)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    libgl1 libxkbcommon-x11-0 libxcb-xinerama0 libxrender1 libxext6 libsm6 libice6 \
    && rm -rf /var/lib/apt/lists/*

# Backend Node deps
WORKDIR /app/backend
COPY backend/package*.json ./
RUN npm ci --omit=dev

# Python deps (PEP 668 safe via venv)
COPY backend/requirements.txt ./
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# Backend source
COPY backend/ ./

# Frontend build artifact
COPY --from=frontend_build /app/frontend/build /app/frontend/build

# Env defaults (override in compose)
ENV PORT=5000 \
    PYTHON_BIN=/opt/venv/bin/python \
    HEADLESS=1 \
    MPLBACKEND=Agg \
    QT_QPA_PLATFORM=offscreen \
    UPLOADS_DIR=/app/backend/uploads \
    OUTPUTS_DIR=/app/backend/outputs \
    FRONTEND_BUILD_DIR=/app/frontend/build

EXPOSE 5000

# server.js è dentro /app/backend
CMD ["node", "server.js"]