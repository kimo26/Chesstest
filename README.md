# Chess Coach

A local, privacy-first chess coaching app. It runs entirely on your
machine: Postgres for your game/knowledge data, Stockfish for analysis,
Ollama for the coach LLM, and lc0 + Maia weights so your opponents in
**Practice** play like real humans at your rating — not brutal engines.

The app is a React web UI plus a FastAPI backend. A persistent **Coach
Widget** floats on every page with conversation memory so you can ask
"why was that a blunder?" without leaving what you're doing. The first
time you launch it, a one-page **onboarding wizard** asks for your
Chess.com username and runs every data pipeline for you, with ETAs and
progress.

---

## Quickstart (non-programmer edition)

**First time:**

```bash
git clone <this repo>
cd Chesstest
./scripts/setup.sh
```

**Later:**

```bash
./scripts/start.sh
```

Open the URL printed by the script, usually `http://localhost:5173`.
First visit: onboarding. Enter your Chess.com username and follow the
steps.

The scripts can install Docker on Linux with your permission, or point
you to Docker Desktop on macOS.

### Docker only

The entire app — Postgres (pgvector), Redis, Ollama, the FastAPI
backend, and the Vite frontend runs in containers defined by
`docker-compose.yml`. There is no bare-metal path; nothing gets
installed into your system Python or your home directory beyond the
repo itself, Docker, and a `./models/` cache for the Maia weights.

- **Linux:** runs Docker's official `get.docker.com` convenience
  script (needs `sudo`), enables the daemon via systemd, and adds you
  to the `docker` group.
- **macOS:** points you at Docker Desktop; if Homebrew is present you
  can say "yes" to `brew install --cask docker`.
- **Decline the prompt** and `setup.sh` exits cleanly — no fallback,
  no half-configured state. Install Docker your own way and re-run.

Non-interactive environments (CI, piped stdin) must pass `--yes` to
accept the prompts; otherwise the script exits rather than silently
installing system-level software.

### GPU (optional)

Most people can ignore this. `setup.sh` and `start.sh` automatically try
the best supported Ollama backend and fall back to CPU if needed.

Optional flags:

```bash
./scripts/start.sh --cpu
./scripts/start.sh --gpu-backend nvidia
```

- `--cpu` skips GPU detection and forces CPU mode
- `--gpu-backend ...` is mainly for debugging or advanced setups
- `--gpu` is still accepted, but no longer needed for the normal path

If GPU acceleration gives you trouble, use `./scripts/start.sh --cpu`
and keep going. Troubleshooting below covers the advanced host details.

---

## What you get

| Page | What it does |
| --- | --- |
| **Dashboard** | Puzzle rating, cards due, weakest openings, quick links. |
| **Practice** | Play against **Maia** at your rating. Opponents come from a **dropdown** populated by Chess.com imports — ranked by where you blunder most. Train a Maia-individual model per opponent. |
| **Puzzles** | Spaced-repetition puzzle trainer, bucketed by your weak openings / tactical themes. |
| **Flashcards** | SM-2 spaced repetition on opening positions and key ideas. |
| **Openings Explorer** | Browse the ECO + Lichess-masters tree, see typical plans, run Stockfish on any node, generate flashcards from a line. |
| **Insights** | Per-game accuracy, blunders/mistakes/inaccuracies, phase performance, move-by-move drill-down. Ask for a coaching summary. |
| **Progress** | Rating trend, weakness chart, import controls, Maia-individual training on specific opponents. |
| **Chat** | Full-screen view of the same coach conversation the widget uses. |
| **Coach Widget** | Floating panel on every page. Tool-calling agent with memory. Knows the page you're on, the position / card / puzzle you're looking at, and the last move you played. |

---

## Architecture

Everything inside the dotted box runs as a Docker container. Maia
weights and ECO TSVs are host-mounted from `./models/` and `./data/`.

```
 ┌─ docker compose ──────────────────────────────────────────────┐
 │                                                               │
 │   frontend (vite)  ─► api (FastAPI)  ─► ollama (qwen+bge-m3)  │
 │        │                 │   │                                │
 │        │                 │   └──► postgres (pgvector)         │
 │        │                 └─► redis                            │
 │        │              stockfish + lc0 built into the api img  │
 │        │                                                      │
 │        └── browser <─── localhost:5173                        │
 │                                                               │
 └───────────────────────────────────────────────────────────────┘
             ./models (Maia weights)  ./data (ECO, wiki, pdfs)
```

- **Postgres 16 + pgvector + pg_trgm + ltree** — games, puzzles,
  flashcards, the ECO / opening tree, RAG documents & chunks with
  embeddings, conversation memory, onboarding state.
- **Stockfish 17** — batch game analysis, per-position evaluation, the
  Openings Explorer "Analyse" button.
- **Ollama** — runs `qwen2.5:32b` (coach), `qwen2.5:14b` (descriptions
  / summaries), `bge-m3` (embeddings). Native tool-calling schema used
  by the agent.
- **lc0 + Maia-1 weights** — the human-like opponent in Practice, plus
  the base for Maia-individual opponent fine-tunes.
- **React 18 + Vite + Chessground + Recharts** — the UI.

---

## The Coach Widget

The coach is a **tool-calling agent**, not a plain RAG chatbot. It has
five tools it can call in any order, any number of times:

| Tool | Purpose |
| --- | --- |
| `rag_search` | Vector search over the knowledge base (ECO descriptions, Wikipedia opening articles, **"Chess Words of Wisdom"** PDF). |
| `stockfish_analyse` | Analyse any FEN at depth/multiPV. |
| `opening_lookup` | Find an opening node by ECO / name / FEN. |
| `my_games_query` | Query the user's own analysed games + weaknesses. |
| `opponent_tendency` | Look up an opponent's frequent deviations from theory. |

The widget:

- **Remembers**: every turn is stored in `rag_messages`; the last ~12
  turns are fed back on each request so the coach has context.
- **Sees what you see**: each page publishes a `PageContext`
  (`{page, fen, last_move, card_id, puzzle_id, eval_cp, …}`) to the
  `CoachProvider`. That context is sent with every question, so you
  can ask "why did that lose?" without retyping the position.
- **Shows its work**: the `tool_trace` for each answer is rendered
  inside a collapsible `<details>` block under the assistant message.
- **Persists**: `conversation_id` is stored in `localStorage` so
  history survives reloads, and the same conversation is visible in
  the dedicated `/chat` page.

Turn it off per-request by passing `use_agent: false` to `/api/chat` —
that falls back to the plain RAG path.

---

## First-run onboarding

On first boot the app sees no user row and redirects to
`/onboarding`. You enter your Chess.com username and the backend
spawns a background task that runs these pipelines in order:

| # | Step | ETA |
| - | ---- | --- |
| 1 | Load ECO openings + Lichess masters tree | ~30 s |
| 2 | Refresh Wikipedia opening articles | ~1–2 min |
| 3 | Ingest knowledge base (wiki + **Chess Words of Wisdom** PDF) | ~10 min |
| 4 | Import your Chess.com games + opponents' games | ~3 min |
| 5 | Generate AI opening descriptions for your repertoire | ~15 min |

The wizard polls `/api/onboarding/status` every two seconds and shows
per-step state (✓ / ✗ / ⟳ / •), detail, error, and a rolling total
ETA. When it's done, it redirects to `/`.

Maia-individual training is deferred: you trigger it per-opponent from
the **Progress** page once onboarding is done, because a fine-tune is
a few minutes per opponent.

---

## Configuration

All env vars are set for you in `docker-compose.yml` with sensible
defaults for the containerised networking (service names, not
`localhost`). Override any of them by adding an `environment:` line to
the `api` service — or by creating a `.env` file at the repo root,
which Compose picks up automatically.

Host port publishing policy:
- Base `docker-compose.yml` exposes only the frontend on the host.
- `docker-compose.host-access.yml` (enabled via `--host-access`) exposes
  API/Ollama/Postgres/Redis on loopback for local tooling.
- If `.env.host-access.local` exists, both scripts auto-load it when
  `--host-access` is used.

| Variable | Default (in compose) | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql://chess:chess@postgres:5432/chess_coach` | Postgres DSN. |
| `REDIS_URL` | `redis://redis:6379/0` | Redis DSN. |
| `OLLAMA_URL` | `http://ollama:11434` | Ollama daemon. |
| `COACH_MODEL` | `qwen2.5:32b-instruct` | Tool-calling coach model. |
| `DESC_MODEL` | `qwen2.5:14b-instruct` | Opening descriptions / summaries. |
| `EMBED_MODEL` | `bge-m3` | Embeddings for RAG. |
| `STOCKFISH_PATH` | `stockfish` | Stockfish (baked into the api image). |
| `LC0_PATH` | `lc0` | lc0 (baked into the api image). |
| `MAIA_WEIGHTS` | `/app/models/maia-1900.pb.gz` | Host-mounted Maia weights. |
| `MAIA_MODELS_DIR` | `/app/models/individuals` | Per-opponent fine-tunes. |

---

## Manual pipelines (optional)

Everything the onboarding wizard runs can also be run by hand, via
`docker compose exec`:

```bash
# ECO + Lichess opening tree
docker compose exec api python -m chess_coach.scripts.load_eco              # ~30 s
docker compose exec api python -m chess_coach.scripts.crawl_lichess         # ~15–30 min
docker compose exec api python -m chess_coach.scripts.refresh_wiki          # ~1–2 min
docker compose exec api python -m chess_coach.scripts.generate_descriptions # dynamic ETA

# Knowledge base (wiki + PDFs + ECO)
docker compose exec api python -m chess_coach.scripts.ingest_knowledge all
# or just the PDF preset:
docker compose exec api python -m chess_coach.scripts.ingest_knowledge pdf-preset chess-wisdom

# Puzzle database
docker compose exec api python -m chess_coach.scripts.extract_puzzles       # dynamic ETA

# Import your games (also pulls your top 10 opponents)
docker compose exec api python -c "import asyncio; from chess_coach.data.chesscom_importer import import_user_games; \
           asyncio.run(import_user_games(USER_ID, 'your_chesscom_handle', \
                                         months_back=12, collect_opponents=True))"

# Train a Maia-individual on a specific opponent
docker compose exec api python -m chess_coach.scripts.train_maia_individual <opponent_handle>
```

Every script prints its ETA before starting.

---

## What `setup.sh` does (and skips)

1. **Docker check.** `command -v docker`. Missing? Prompt before
   installing. Linux → `get.docker.com` script + `usermod -aG docker`.
   macOS → Docker Desktop (optionally via `brew install --cask docker`).
2. **Compose plugin check.** Installs `docker-compose-plugin` on
   Linux if missing (also prompted).
3. **GPU mode** auto-detects or forces one of the backend-specific Ollama
   overrides: NVIDIA, ROCm, Vulkan, or CPU. `--cpu` disables GPU
   detection; `--gpu-backend ...` forces a specific path.
4. **Host-mounted assets:** downloads `maia-1900.pb.gz` into `./models/`
   and clones `lichess-org/chess-openings` into `./data/` — both are
   skipped if already present.
5. **`docker compose build`** — caches the lc0 compile across runs.
6. **`docker compose up -d`** — on first boot, Postgres auto-applies
   every file in `./sql/` via `/docker-entrypoint-initdb.d/`. By
   default, only the frontend is host-published; pass `--host-access`
   to expose API/Ollama/Postgres/Redis on localhost.
7. **Ollama model pulls:** `ollama pull qwen2.5:32b-instruct`,
   `qwen2.5:14b-instruct`, `bge-m3` — skipped if `ollama list` shows
   them already.

Re-running is safe: every step prints "✓ already present" when nothing
needs doing.

---

## Troubleshooting

- **"Docker is not installed"** — say "yes" to the prompt, or install
  Docker yourself ([docs](https://docs.docker.com/engine/install/))
  and re-run `./scripts/setup.sh`.
- **"Cannot talk to the Docker daemon"** — you were just added to the
  `docker` group; open a new shell or run `newgrp docker`. On macOS
  make sure Docker Desktop is running.
- **GPU mode picked the wrong backend for your host** — force one
  explicitly while debugging:
  - `./scripts/start.sh --gpu-backend nvidia`
  - `./scripts/start.sh --gpu-backend rocm`
  - `./scripts/start.sh --gpu-backend vulkan`
  - `./scripts/start.sh --cpu`
- **`nvidia-container-cli ... libnvidia-ml.so.1`** — the NVIDIA host
  runtime is missing/broken.
  - Fastest fix: `./scripts/start.sh --cpu`
  - NVIDIA fix: install/reinstall
    [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html),
    then retry `./scripts/start.sh` or `./scripts/start.sh --gpu-backend nvidia`.
- **ROCm / Vulkan device errors** — the required device nodes are missing
  or blocked on the host.
  - ROCm needs `/dev/kfd` and `/dev/dri`
  - Vulkan needs `/dev/dri`
  - Fastest fix: `./scripts/start.sh --cpu`
- **Port conflicts**
  - Default mode exposes only the frontend host port.
  - In `--host-access` mode, API/Ollama/Postgres/Redis are also
    published on localhost.
  - In both modes, scripts auto-pick nearby free host ports and print
    the selected values.
- **`DOCKER-ISOLATION-STAGE-2` / iptables chain missing** — Docker lost
  its bridge firewall chains (often after firewall/backend changes).
  Recover with:
  ```bash
  sudo systemctl restart docker
  docker compose down --remove-orphans || true
  docker network prune -f
  ./scripts/start.sh
  ```
  `setup.sh` and `start.sh` both include one automatic recovery attempt,
  but host firewall policy is machine-specific and intentionally not
  hardcoded in this repo.
- **Ollama pulls are slow / fail** — they're many GB. Retry with
  `docker compose exec ollama ollama pull qwen2.5:32b-instruct`.
- **Rebuild after `pyproject.toml` change** — `./scripts/start.sh -b`
  or `docker compose build api`.
- **Wipe everything** — `docker compose down -v` drops the named
  volumes (`pgdata`, `redisdata`, `ollama`) and you're back to a clean
  slate. `./models/` and `./data/` are preserved.

---

## Extending

**Add a RAG source.** Drop a PDF in `data/pdfs/` and:

```bash
docker compose exec api python -m chess_coach.scripts.ingest_knowledge pdf /app/data/pdfs/yourfile.pdf
```

Or add an entry to `PDF_PRESETS` in
`chess_coach/data/knowledge_ingest.py` and re-run `ingest_knowledge all`.

**Add a coach tool.** Add a new entry in the `TOOLS` list in
`chess_coach/agents/coach.py` with a JSON schema, then implement its
handler in the `_dispatch` dict. The next question will have it
available in the tool-call loop.

**Add a page context.** Extend the `PageContext` union in
`frontend/src/types/index.ts`, call `useCoachPageContext({…})` from
your page, and teach `coach.py`'s system prompt to react to the new
fields.

---

## License

See `LICENSE`.
