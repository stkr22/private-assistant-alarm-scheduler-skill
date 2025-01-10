# Build stage: Python 3.12.8-bookworm
FROM docker.io/library/python@sha256:b3edcabc927022dfa922374a8f609afb66900d54a483296b7b7d6cee402ec753 as build-python

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

# Install uv.
COPY --from=ghcr.io/astral-sh/uv:0.5.14 /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Copy the application into the container.
COPY pyproject.toml README.md uv.lock /app
COPY src /app/src

RUN --mount=type=cache,target=/root/.cache \
    cd /app && \
    uv sync \
        --frozen \
        --no-group dev \
        --group prod

# runtime stage: Python 3.12.8-slim-bookworm
FROM docker.io/library/python@sha256:10f3aaab98db50cba827d3b33a91f39dc9ec2d02ca9b85cbc5008220d07b17f3

ENV PYTHONUNBUFFERED=1

# Create non-root user
RUN addgroup --system --gid 1001 appuser && adduser --system --uid 1001 --no-create-home --ingroup appuser appuser

WORKDIR /app
COPY --from=build-python /app /app

ENV PATH="/app/.venv/bin:$PATH"
# Set the user to 'appuser'
USER appuser

CMD ["private-assistant-alarm-scheduler-skill"]
