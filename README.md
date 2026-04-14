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

```bash
git clone <this repo>
cd Chesstest
./scripts/setup.sh      # idempotent; installs only what's missing
./scripts/start.sh      # boots API + UI, prints the URL
```

Open the URL it prints (usually `http://localhost:5173`). The first
launch drops you on `/onboarding`; type your Chess.com username, click
**Start**, and watch each step tick green.

Already have Postgres/Stockfish/Ollama/lc0 on this machine? `setup.sh`
detects them and skips. Don't? It installs them via your package
manager (apt / brew / dnf), pulls the required Ollama models, downloads
the Maia-1 weights, and provisions the database.

### Optional: Docker Compose path

Don't want system-level Postgres/Redis/Ollama? Run setup with Docker:

```bash
./scripts/setup.sh --docker
```

This writes a `docker-compose.yml` with `pgvector/pgvector:pg16`,
`redis:7-alpine`, and `ollama/ollama:latest`, and brings them up. Only
Stockfish, Python, Node, and lc0 stay native.

### GPU build of lc0

For a faster Maia in Practice:

```bash
./scripts/setup.sh --gpu
```

(lc0 is built from source with CUDA if `nvidia-smi` is present.)

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

```
            ┌────────────────────────────────────────────┐
            │                React (Vite)                │
            │  /dashboard  /practice  /puzzles  /…       │
            │  CoachProvider  ──►  <CoachWidget/>        │
            └────────────────┬───────────────────────────┘
                             │ /api/*  (vite proxy)
                             ▼
            ┌────────────────────────────────────────────┐
            │        FastAPI (chess_coach.api)           │
            │  routes: practice puzzles flashcards       │
            │          openings chat insights onboarding │
            │          opponents training                │
            │  agents/coach.py  ←─ tool-calling coach    │
            └───┬──────────┬──────────┬──────────┬───────┘
                │          │          │          │
                ▼          ▼          ▼          ▼
           Postgres+    Stockfish    Ollama     lc0 +
           pgvector      (UCI)      (chat+emb)  Maia-1
                │
                └── rag_messages  games  puzzles  flashcards
                    knowledge_chunks  opening_nodes
                    onboarding_state
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

`chess_coach/config.py` reads env vars:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql://chess_coach@localhost/chess_coach` | Postgres connection. |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama daemon. |
| `COACH_MODEL` | `qwen2.5:32b` | Main coach model (tool calling). |
| `DESC_MODEL` | `qwen2.5:14b` | Opening descriptions / coaching summaries. |
| `EMBED_MODEL` | `bge-m3` | Embeddings for RAG. |
| `STOCKFISH_PATH` | `stockfish` | Stockfish binary. |
| `LC0_PATH` | `lc0` | lc0 binary. |
| `MAIA_WEIGHTS` | `./data/maia/maia-1900.pb.gz` | Maia-1 weights. |
| `MAIA_MODELS_DIR` | `./data/maia/individuals/` | Per-opponent fine-tunes. |

Put overrides in a `.env` file at the repo root.

---

## Manual pipelines (optional)

Everything the onboarding wizard runs can also be run by hand:

```bash
# ECO + Lichess opening tree
python -m chess_coach.scripts.load_eco                  # ~30 s
python -m chess_coach.scripts.crawl_lichess             # ~15–30 min
python -m chess_coach.scripts.refresh_wiki              # ~1–2 min
python -m chess_coach.scripts.generate_descriptions     # dynamic ETA

# Knowledge base (wiki + PDFs + ECO)
python -m chess_coach.scripts.ingest_knowledge all
# or just the PDF preset:
python -m chess_coach.scripts.ingest_knowledge pdf-preset chess-wisdom

# Puzzle database
python -m chess_coach.scripts.extract_puzzles           # dynamic ETA

# Import your games (also pulls your top 10 opponents)
python -c "import asyncio; from chess_coach.data.chesscom_importer import import_user_games; \
           asyncio.run(import_user_games(USER_ID, 'your_chesscom_handle', \
                                         months_back=12, collect_opponents=True))"

# Train a Maia-individual on a specific opponent
python -m chess_coach.scripts.train_maia_individual <opponent_handle>
```

Every script prints its ETA before starting.

---

## What `setup.sh` installs (and skips)

For each item, `setup.sh` probes with the right tool (`dpkg -s`,
`brew list --formula`, `rpm -q`, `command -v`, `ollama list`) before
installing. A second `./scripts/setup.sh` run should print "✓ already
installed" for every step.

- System: `postgresql-16`, `postgresql-16-pgvector`, `redis-server`,
  `stockfish`, `python3.11 + venv`, `nodejs + npm`, `curl`, `git`,
  `build-essential`, `zstd`.
- **Python**: creates `.venv`, runs `pip install -e .`.
- **Node**: runs `npm install` only if `package-lock.json` is newer
  than `frontend/node_modules`.
- **Ollama**: installs daemon if missing; pulls `qwen2.5:32b`,
  `qwen2.5:14b`, `bge-m3` only if not already in `ollama list`.
- **lc0**: builds from source (CPU, or CUDA with `--gpu`) only if
  `lc0` isn't on `$PATH`.
- **Maia weights**: downloads `maia-1900.pb.gz` only if absent.
- **Postgres**: creates role + database only if missing; applies
  `sql/001_schema.sql`, `sql/002_knowledge_base.sql`,
  `sql/003_coach_memory.sql` (all `IF NOT EXISTS`).

---

## Troubleshooting

- **Ollama daemon not running** — `ollama serve &` or
  `systemctl --user start ollama`. `start.sh` does this for you on
  boot.
- **Postgres peer-auth rejection** — the setup script creates a role
  matching `$USER`. If you ran it as root, `sudo -u postgres
  createuser $USER`.
- **`vector type does not exist`** — pgvector isn't installed. On
  Debian/Ubuntu: `sudo apt install postgresql-16-pgvector`. Or use
  `--docker`.
- **lc0 build fails** — install `libopenblas-dev meson ninja-build
  cmake` and re-run `setup.sh`.
- **Port conflict on 8000 / 5173** — edit `scripts/start.sh`.
- **CORS errors in dev** — the API has `allow_origins=["*"]` and Vite
  proxies `/api`, so this shouldn't happen; check you're hitting
  `localhost:5173`, not `127.0.0.1`.

---

## Extending

**Add a RAG source.** Drop a PDF in `data/pdfs/` and:

```bash
python -m chess_coach.scripts.ingest_knowledge pdf path/to/file.pdf
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
