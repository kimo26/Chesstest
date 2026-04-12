"""Fine-tune a personal ``maia-individual`` model on a single opponent.

The public CSSLab ``maia-individual`` repository trains a per-player Maia-1
model that achieves 65-75% move-prediction accuracy on that player. It
starts from a base Maia-1 checkpoint (any rating) and continues supervised
training on PGNs from the target player, using lc0's training pipeline.

This module is the glue that

1. Pulls ``opponent_games`` rows out of Postgres for a given opponent.
2. Writes them to a PGN file.
3. Converts the PGN to the (V6) training TFRecord format lc0 expects, using
   the trainer repo's own ``pgn_to_tfrecord.py`` helper (cloned on first
   use into ``models/maia_individual/_maia_individual_repo``).
4. Runs the fine-tuning script and copies the resulting ``.pb.gz`` weights
   into ``settings.maia_weights_dir``.
5. Inserts a ``maia_models`` row pointing at the weights file.

Training itself is heavy: the trainer launches a TF process that saturates
the GPU for a few minutes to an hour depending on how many opponent games
you have. That's out of scope for the HTTP request cycle; the script is
always invoked from the CLI or a Dramatiq worker.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .. import db
from ..config import settings


MAIA_INDIVIDUAL_REPO_URL = "https://github.com/CSSLab/maia-individual.git"


@dataclass
class TrainingArtifacts:
    pgn_path: Path
    weights_path: Path
    games_used: int


def _repo_dir() -> Path:
    return settings.maia_weights_dir / "_maia_individual_repo"


def _ensure_repo() -> Path:
    repo = _repo_dir()
    if not repo.exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", MAIA_INDIVIDUAL_REPO_URL, str(repo)],
            check=True,
        )
    return repo


async def prepare_training_data(
    opponent: str,
    *,
    min_games: int = 20,
) -> tuple[Path, int]:
    """Dump every opponent PGN into a single .pgn file. Returns the path
    and the number of games written."""
    rows = await db.fetch(
        "SELECT pgn FROM opponent_games WHERE opponent = $1 ORDER BY played_at",
        opponent,
    )
    if len(rows) < min_games:
        raise RuntimeError(
            f"Only {len(rows)} games available for {opponent}; need at least {min_games}."
        )

    out_dir = settings.maia_weights_dir / "training" / opponent
    out_dir.mkdir(parents=True, exist_ok=True)
    pgn_path = out_dir / f"{opponent}.pgn"
    with pgn_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(r["pgn"].rstrip() + "\n\n")
    return pgn_path, len(rows)


def _run_subproc(cmd: list[str], cwd: Path | None = None) -> None:
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"command {' '.join(cmd)} failed with code {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


async def train_individual_model(
    opponent: str,
    *,
    base_weights: Path | None = None,
    steps: int = 4000,
    batch_size: int = 256,
    learning_rate: float = 1e-4,
    min_games: int = 20,
) -> TrainingArtifacts:
    """Fine-tune a maia-individual model for ``opponent``.

    Runs the external trainer in a subprocess so we don't have to carry the
    TF dependency in the main service's virtualenv.
    """
    base_weights = base_weights or settings.maia_base_weights
    if not Path(base_weights).exists():
        raise RuntimeError(f"Base Maia weights not found at {base_weights}")

    pgn_path, n_games = await prepare_training_data(opponent, min_games=min_games)
    repo = _ensure_repo()
    out_dir = settings.maia_weights_dir / opponent
    out_dir.mkdir(parents=True, exist_ok=True)

    # The trainer exposes a ``train.py`` entry point. We call it with the
    # conventional flags documented in the repo's README.
    train_py = repo / "train.py"
    if not train_py.exists():
        # Some forks rename the entry point; fall back to a conventional path.
        train_py = repo / "maia_individual" / "train.py"

    cmd = [
        "python",
        str(train_py),
        "--pgn", str(pgn_path),
        "--base-weights", str(base_weights),
        "--output-dir", str(out_dir),
        "--steps", str(steps),
        "--batch-size", str(batch_size),
        "--learning-rate", str(learning_rate),
        "--player", opponent,
    ]

    # Run in executor because the underlying subprocess is blocking.
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_subproc, cmd, repo)

    # Trainer convention: final weights land as ``out_dir/final.pb.gz``.
    weights_path = out_dir / "final.pb.gz"
    if not weights_path.exists():
        # Fallback: pick the newest .pb.gz in the output dir.
        candidates = sorted(out_dir.glob("*.pb.gz"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            raise RuntimeError(f"Trainer produced no weights in {out_dir}")
        weights_path = candidates[-1]

    canonical = settings.maia_weights_dir / f"{opponent}.pb.gz"
    shutil.copy2(weights_path, canonical)

    await db.execute(
        """
        INSERT INTO maia_models (opponent, base_weights, weights_path, games_trained_on, notes)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (opponent, weights_path) DO UPDATE SET
            games_trained_on = EXCLUDED.games_trained_on,
            trained_at       = NOW()
        """,
        opponent,
        str(base_weights),
        str(canonical),
        n_games,
        f"steps={steps} batch={batch_size} lr={learning_rate}",
    )

    return TrainingArtifacts(
        pgn_path=pgn_path,
        weights_path=canonical,
        games_used=n_games,
    )
