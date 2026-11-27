# The crew's container (plan 8.6, spec S8 R7). Code as action is off, and the image holds no key and no data.
# Build:  docker build -t computers-crew .
# Test:   docker run --rm --network none computers-crew
# Run a live job: pass the key at run time with `-e OPENROUTER_API_KEY` (never bake it into an image).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=0 UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 crew && mkdir /app && chown crew:crew /app
WORKDIR /app
COPY --chown=crew:crew . /app
USER crew

# dev for pytest, crew for NOOA and pydantic, mcp for the servers. The torch group is not needed here.
RUN uv sync --frozen --group dev --group crew --group mcp

CMD ["uv", "run", "--no-sync", "pytest", "-q", "-m", "not network and not slow"]
