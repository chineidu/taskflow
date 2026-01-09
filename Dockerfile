# ==============================================================================
# Stage 1: Base Setup
# Use Slim Linux for minimal base image size
# ==============================================================================
FROM python:3.13-slim AS python_base

# Python optimizations to avoid buffering issues and improve performance
ENV PYTHONUNBUFFERED=1
# Enable bytecode compilation for faster imports
ENV UV_COMPILE_BYTECODE=1
# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy

# Set working directory for all subsequent commands
WORKDIR /app

# ==============================================================================
# Stage 2: Builder - Install dependencies in isolation
# ==============================================================================
FROM python_base AS builder
# Copy uv package manager from official image for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies using cache mount to avoid re-downloading
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ==============================================================================
# Stage 3: Production - Final lightweight image
# ==============================================================================
FROM python_base AS prod

# Create non-root user for security best practices
RUN groupadd -r appuser \
    && useradd -r -g appuser -d /app appuser

WORKDIR /app

# Create directories that the app needs to write to, with proper ownership
RUN mkdir -p models \
    && chown -R appuser:appuser models

# Copy pre-built virtual environment from builder stage
COPY --from=builder /app/.venv /app/.venv

# Copy application source code and configuration files (respecting .dockerignore)
# Assign ownership to non-root user
COPY --chown=appuser:appuser . /app

# Make startup scripts executable (must be done before switching to non-root user)
RUN chmod +x /app/docker/*.sh

# Switch to non-root user to run application
USER appuser

# Add virtual environment to PATH for direct command access
ENV PATH="/app/.venv/bin:$PATH"

# ==============================================================================
# Entry point: Default command to run the application
# `CMD`: Used to start the main application at RUN time and NOT build time
# CMD can be overridden at runtime as needed BUT ENTRYPOINT cannot
# Here we default to starting the main app server
# ==============================================================================
CMD ["bash", "docker/run.sh"]