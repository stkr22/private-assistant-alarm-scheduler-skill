# Build stage: Python 3.12.8-bookworm
FROM docker.io/library/python@sha256:d57ec66c94b9497b9f3c66f6cdddc1e4e0bad4c584397e0b57a721baef0e6fdc as build-python

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
FROM docker.io/library/python@sha256:d57ec66c94b9497b9f3c66f6cdddc1e4e0bad4c584397e0b57a721baef0e6fdc

ENV PYTHONUNBUFFERED=1

# Create non-root user
RUN addgroup --system --gid 1001 appuser && adduser --system --uid 1001 --no-create-home --ingroup appuser appuser

WORKDIR /app
COPY --from=build-python /app /app

ENV PATH="/app/.venv/bin:$PATH"
# Set the user to 'appuser'
USER appuser

CMD ["private-assistant-alarm-scheduler-skill"]
