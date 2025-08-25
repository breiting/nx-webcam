# Copyright (C) 2025 Bernhard Reitinger
#
# THIS CODE AND INFORMATION ARE PROVIDED "AS IS" WITHOUT WARRANTY OF ANY
# KIND, EITHER EXPRESSED OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND/OR FITNESS FOR A
# PARTICULAR PURPOSE.

ARG BASE_IMAGE_NAME="python"
ARG BASE_IMAGE_VERSION="3.12.8-slim"
ARG BASE_IMAGE="${BASE_IMAGE_NAME}:${BASE_IMAGE_VERSION}"

FROM ${BASE_IMAGE} AS runtime

# --- uv bereitstellen ---
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# --- Timezone ---
ARG TIMEZONE="Europe/Vienna"
RUN ln -snf /usr/share/zoneinfo/${TIMEZONE} /etc/localtime && echo ${TIMEZONE} > /etc/timezone

# --- System-Pakete für Video & OpenCV/GStreamer ---
# libgl1 & libglib2.0 für OpenCV; v4l-utils für Kamera-Debugging;
# GStreamer-Plugins inkl. H.264-Decoder (libav) für CAP_GSTREAMER
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 v4l-utils curl \
    gstreamer1.0-tools gstreamer1.0-libav \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
 && rm -rf /var/lib/apt/lists/*

# --- Arbeitsverzeichnis & Abhängigkeiten (uv) ---
WORKDIR /app
ENV UV_PYTHON_PREFERENCE=system
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

# nur manifest(e) copyen für layer-caching
COPY pyproject.toml ./
# optional: Lockfile, falls vorhanden
# COPY uv.lock ./

# Dependencies installieren (ohne Dev)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev || uv sync --no-dev

# App-Code
COPY app ./app

# Port & Healthcheck
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=5 CMD curl -fsS http://localhost:8000/snapshot.jpg >/dev/null || exit 1

# Laufbefehl
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
