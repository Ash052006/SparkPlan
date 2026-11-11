# ==============================================================================
# Multi-Stage Production Dockerfile for SparkPlan
# Stage 1: Build React/Vite/Three.js frontend
# Stage 2: Provision arduino-cli and pre-cache ESP32 silicon board core + libs
# Stage 3: Python runtime with CadQuery / OpenCascade system libraries
# ==============================================================================

# --- Stage 1: Frontend Build ---
FROM node:20-alpine AS frontend-builder
WORKDIR /build/web

COPY web/package*.json ./
RUN npm ci --prefer-offline --no-audit

COPY web/ ./
RUN npm run build

# --- Stage 2: Embedded Hardware Toolchain ---
FROM python:3.11-slim-bookworm AS toolchain-builder
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    tar \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install arduino-cli
RUN curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | BINDIR=/usr/local/bin sh

# Initialize arduino-cli config and download ESP32 core
ENV ARDUINO_DIRECTORIES_DATA=/root/.arduino15
RUN arduino-cli config init && \
    arduino-cli config set board_manager.additional_urls https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json && \
    arduino-cli core update-index && \
    arduino-cli core install esp32:esp32@2.0.14 && \
    arduino-cli lib install "DHT sensor library" "Adafruit SSD1306" "Adafruit GFX Library" "Adafruit Unified Sensor" "LiquidCrystal I2C"

# --- Stage 3: Production Runtime ---
FROM python:3.11-slim-bookworm AS final
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SPARKPLAN_ARDUINO_CLI=/usr/local/bin/arduino-cli \
    ARDUINO_DIRECTORIES_DATA=/home/sparkplan/.arduino15

# Install system dependencies for OpenCascade / CadQuery / headless graphics
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package resolution
COPY --from=ghcr.io/astral-sh/uv:0.4.18 /uv /usr/local/bin/uv

# Copy arduino-cli binary and pre-cached ESP32 cores from Stage 2
COPY --from=toolchain-builder /usr/local/bin/arduino-cli /usr/local/bin/arduino-cli
COPY --from=toolchain-builder /root/.arduino15 /home/sparkplan/.arduino15

# Create non-root user
RUN groupadd -g 1000 sparkplan && \
    useradd -u 1000 -g sparkplan -m -s /bin/bash sparkplan && \
    chown -R sparkplan:sparkplan /home/sparkplan

WORKDIR /app

# Copy dependency manifests and install dependencies
COPY pyproject.toml .
RUN uv pip install --system -r pyproject.toml

# Copy application source code
COPY catalog/ catalog/
COPY sparkplan/ sparkplan/
COPY api/ api/
COPY Makefile .

# Copy pre-built frontend distribution from Stage 1
COPY --from=frontend-builder /build/web/dist /app/web/dist

RUN chown -R sparkplan:sparkplan /app
USER sparkplan

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
