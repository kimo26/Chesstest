"""Central configuration. Everything is loaded from environment variables so
the same code runs locally, in docker-compose, and in production.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql://chess:chess@localhost:5432/chess_coach"
    db_pool_min: int = 2
    db_pool_max: int = 16

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Ollama
    ollama_url: str = "http://localhost:11434"
    ollama_gen_model: str = "qwen2.5:32b-instruct"
    ollama_fast_model: str = "qwen2.5:14b-instruct"
    ollama_embed_model: str = "bge-m3"
    ollama_embed_dims: int = 1024
    ollama_timeout_s: float = 180.0
    ollama_gen_num_ctx: int = 8192
    ollama_gen_temperature: float = 0.2

    # Engines
    stockfish_path: str = "/usr/local/bin/stockfish"
    stockfish_threads: int = 8
    stockfish_hash_mb: int = 2048
    lc0_path: str = "/usr/local/bin/lc0"
    lc0_threads: int = 2
    lc0_nodes: int = 1  # maia is designed to play at 1 node — no search

    # Maia weights
    maia_weights_dir: Path = Path("./models/maia_individual")
    maia_base_weights: Path = Path("./models/maia-1900.pb.gz")

    # External
    lichess_explorer_url: str = "https://explorer.lichess.ovh"
    chess_com_api_url: str = "https://api.chess.com/pub"
    user_agent: str = "chess_coach/0.1 (+https://example.local)"

    # Retrieval
    rag_top_k_dense: int = 20
    rag_top_k_bm25: int = 20
    rag_rrf_k: int = 60
    rag_final_top_k: int = 6

    # Stockfish analysis (API endpoint)
    stockfish_analysis_depth: int = 22
    stockfish_analysis_multipv: int = 3
    stockfish_mistake_threshold_cp: int = 100

    # Puzzle extraction
    puzzle_swing_cp: int = 200
    puzzle_second_best_gap_cp: int = 100
    puzzle_stockfish_nodes: int = 5_000_000


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
