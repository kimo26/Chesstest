# ──────────────────────────────────────────────────────────────────────
# API image: Python 3.11 + Stockfish + lc0 (built from source).
# Multi-stage so the final image doesn't ship the C++ toolchain.
# ──────────────────────────────────────────────────────────────────────
FROM debian:bookworm-slim AS lc0-builder
RUN apt-get update && apt-get install -y --no-install-recommends \
      git build-essential meson ninja-build libopenblas-dev \
      pkg-config ca-certificates python3 \
 && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/LeelaChessZero/lc0 /src/lc0
WORKDIR /src/lc0
# CPU + BLAS only — fine for the Maia-1 weights we ship.
RUN ./build.sh release -Dblas=true -Dcudnn=false -Dplain_cuda=false \
 && strip build/release/lc0

# ──────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      stockfish \
      libopenblas0 \
      curl ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

COPY --from=lc0-builder /src/lc0/build/release/lc0 /usr/local/bin/lc0

WORKDIR /app

# Install Python deps first so Docker can cache this layer across code edits.
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip wheel \
 && pip install --no-cache-dir \
      fastapi 'uvicorn[standard]' websockets pydantic pydantic-settings \
      httpx asyncpg 'psycopg[binary]' numpy chess fsrs 'dramatiq[redis]' \
      redis tenacity structlog typer python-dotenv tqdm pypdf

COPY chess_coach ./chess_coach
COPY sql         ./sql
RUN pip install --no-cache-dir -e .

EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "chess_coach.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
