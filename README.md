# Chess Coach — Local, Personalized Openings Trainer

A fully local chess openings coach targeting a single RTX 5090 workstation.
Every LLM call goes through **Ollama**, and practice games are played by
**maia-individual** networks fine-tuned on each of your actual opponents'
Chess.com game histories and served through **lc0** over UCI.

## What it does

1. **Data** — crawls the Lichess opening explorer, merges the `lichess-org/chess-openings`
   ECO TSV, and imports your (and your opponents') Chess.com game histories.
2. **RAG** — generates opening descriptions with a local LLM, chunks them at
   family / variation / position levels, embeds with `bge-m3` via Ollama,
   stores everything in PostgreSQL (pgvector + tsvector), and retrieves with
   BM25 + dense hybrid search fused via RRF.
3. **Puzzles** — Stockfish 17 scans your games for eval-swing blunders and
   extracts tactical puzzles; the LLM writes hints and explanations. Puzzle
   difficulty is tracked with Glicko-2.
4. **Flashcards** — FSRS v6 scheduler drives move/concept/plan/trap cards
   generated from opening theory by the local LLM.
5. **Practice** — plays full games against `maia-individual` weights
   specifically fine-tuned on a single opponent's games, served via lc0/UCI.
   After the game, Stockfish analyses it and the LLM writes a debrief.
6. **Weakness loop** — a background worker polls Chess.com, updates per-opening
   weakness scores, and generates targeted new puzzles and flashcards.

## Stack

| Layer | Choice |
| --- | --- |
| DB | PostgreSQL 16 + pgvector + ltree + pg_trgm |
| Vector index | HNSW (1024 dims, cosine) |
| Embeddings | `bge-m3` via Ollama |
| Generation LLM | `qwen2.5:32b-instruct` via Ollama (configurable) |
| Chess engine (analysis) | Stockfish 17 NNUE |
| Chess engine (practice) | lc0 loading `maia-individual` per-opponent weights |
| Spaced repetition | `fsrs` (v6) |
| Chess logic | `python-chess` |
| API | FastAPI + WebSockets |
| Queue | Redis + Dramatiq |

## Recommended Ollama models for an RTX 5090 (32 GB VRAM)

```bash
ollama pull qwen2.5:32b-instruct          # primary generation (Q4_K_M ~20 GB)
ollama pull qwen2.5:14b-instruct          # fast path for flashcards / hints
ollama pull bge-m3                        # 1024-dim multilingual embeddings
```

Anything in `settings.py` can be swapped — e.g. `llama3.3:70b-instruct-q4_K_M`,
`gemma3:27b`, or `deepseek-r1:32b` — without touching the rest of the code.

## Quickstart

```bash
# 1. Infra
docker compose up -d postgres redis
psql $DATABASE_URL -f sql/001_schema.sql

# 2. Python env
uv venv && source .venv/bin/activate
uv pip install -e .

# 3. Local services
ollama serve &                    # on :11434
lc0 --weights=<base_maia.pb.gz>   # only needed if you skip practice

# 4. Seed data
python -m chess_coach.scripts.load_eco
python -m chess_coach.scripts.crawl_lichess --max-depth 12
python -m chess_coach.scripts.generate_descriptions
python -m chess_coach.scripts.import_games --user <your_chess_com_handle>

# 5. Fine-tune a maia on an opponent
python -m chess_coach.scripts.train_maia_individual \
    --opponent <their_chess_com_handle> \
    --base-weights models/maia-1900.pb.gz

# 6. Run the app
uvicorn chess_coach.api.main:app --reload
```

## Repo layout

```
chess_coach/
  config.py              settings loaded from env
  db.py                  async pg pool + helpers
  llm/                   Ollama chat + embedding clients, prompt templates
  data/                  Lichess explorer crawler, Chess.com importer, ECO loader
  rag/                   chunker, embedder, hybrid retriever, reranker, generator
  engine/                Stockfish wrapper + puzzle extractor
  maia/                  maia-individual fine-tuning pipeline + lc0 UCI wrapper
  fsrs_cards/            FSRS v6 scheduler + LLM card generator
  analysis/              weakness scoring + post-game debrief
  api/                   FastAPI app, routes, WebSocket practice loop
  scripts/               CLI entry points
sql/001_schema.sql       full database schema
```
