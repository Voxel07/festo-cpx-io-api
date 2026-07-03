# =============================================================================
# Multi-stage Dockerfile for Festo CPX-IO Test System
#
# Build context: parent directory containing all three repos
#   docker build --file festo-cpx-io-api/Dockerfile ..
#
# Produces a single image with:
#   - Pre-built React GUI (static assets served by FastAPI)
#   - FastAPI backend (uvicorn)
#   - festo-cpx-io library (pip-installed)
#
# Adding a new Python library:
#   COPY festo-cpx-io-new-lib/ /build/festo-cpx-io-new-lib/
#   RUN pip install --no-cache-dir /build/festo-cpx-io-new-lib/
# =============================================================================

# ── Stage 1: Build the React GUI ─────────────────────────────────────────────
FROM node:22-slim AS gui-builder

WORKDIR /gui-build

# Install dependencies first (layer cache)
COPY festo-cpx-io-gui/package.json festo-cpx-io-gui/package-lock.json ./
RUN npm install --ignore-scripts

# Copy source and build
COPY festo-cpx-io-gui/ ./
# Build with vite directly (skip tsc type-check for container builds)
RUN npx vite build --outDir /gui-build/dist --emptyOutDir


# ── Stage 2: Python runtime ──────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# System dependencies for pyserial (USB/serial) and general build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libc6-dev \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Install Python libraries ─────────────────────────────────────────────────
# festo-cpx-io (core library)
COPY festo-cpx-io/ /build/festo-cpx-io/
RUN pip install --no-cache-dir /build/festo-cpx-io/

# Add future libraries here:
# COPY festo-cpx-io-new-lib/ /build/festo-cpx-io-new-lib/
# RUN pip install --no-cache-dir /build/festo-cpx-io-new-lib/

# Clean up build artifacts
RUN rm -rf /build

# ── Install API dependencies ─────────────────────────────────────────────────
COPY festo-cpx-io-api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# ── Copy API source code ─────────────────────────────────────────────────────
COPY festo-cpx-io-api/ ./
# Remove files not needed in the container
RUN rm -f requirements.txt .env .gitignore

# ── Copy pre-built GUI assets ────────────────────────────────────────────────
COPY --from=gui-builder /gui-build/dist ./dist

# ── Runtime configuration ────────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Default entrypoint: start the FastAPI server
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
