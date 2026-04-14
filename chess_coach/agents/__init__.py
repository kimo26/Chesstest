"""Multi-agent coach orchestrator.

This package wraps the heterogeneous tool set (RAG, Stockfish, lc0,
Wikipedia, user-game DB) behind a single tool-calling LLM loop. See
``coach.py`` for the main entry point.
"""
from .coach import answer as coach_answer, ToolCallTrace  # noqa: F401

__all__ = ["coach_answer", "ToolCallTrace"]
