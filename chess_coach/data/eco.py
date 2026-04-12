"""Loader for the lichess-org/chess-openings ECO TSVs.

The upstream repo ships five TSVs (a.tsv through e.tsv). Each row is:

    eco<TAB>name<TAB>pgn<TAB>uci<TAB>epd

We ingest all five, resolve PGN/UCI to a python-chess ``Board``, and upsert a
node per line into ``opening_nodes`` keyed by canonical FEN. The rows don't
form a tree on their own, so after insertion we run ``link_parents`` which
walks each move sequence prefix and sets ``parent_id`` / ``path`` / ``depth``.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import chess
import chess.pgn
import io

from .. import db


ECO_TSV_FILES = ("a.tsv", "b.tsv", "c.tsv", "d.tsv", "e.tsv")


@dataclass
class EcoEntry:
    eco: str
    name: str
    pgn: str
    uci: str
    epd: str

    @property
    def uci_moves(self) -> list[str]:
        return self.uci.strip().split()


def _read_tsv(path: Path) -> Iterable[EcoEntry]:
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            yield EcoEntry(
                eco=row["eco"].strip(),
                name=row["name"].strip(),
                pgn=row.get("pgn", "").strip(),
                uci=row.get("uci", "").strip(),
                epd=row.get("epd", "").strip(),
            )


def load_eco_tsv(directory: Path) -> list[EcoEntry]:
    """Load all five TSVs from ``directory`` and return one flat list."""
    entries: list[EcoEntry] = []
    for fname in ECO_TSV_FILES:
        p = directory / fname
        if not p.exists():
            continue
        entries.extend(_read_tsv(p))
    return entries


def _pgn_to_san_sequence(pgn: str) -> list[str]:
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        return []
    return [game.board().san(m) for m in game.mainline_moves()]


def _pretty_move_sequence(uci_moves: list[str]) -> tuple[str, chess.Board]:
    """Play out the UCI moves on a fresh board and return a nicely-formatted
    SAN move sequence ('1.e4 c5 2.Nf3 d6...') plus the final board."""
    board = chess.Board()
    parts: list[str] = []
    for i, umove in enumerate(uci_moves):
        move = chess.Move.from_uci(umove)
        san = board.san(move)
        if i % 2 == 0:
            parts.append(f"{i // 2 + 1}.{san}")
        else:
            parts.append(san)
        board.push(move)
    return " ".join(parts), board


async def upsert_eco_entries(entries: Iterable[EcoEntry]) -> int:
    """Insert (or update) opening_nodes for every ECO entry. Returns the
    number of rows affected."""
    n = 0
    async with db.acquire() as conn:
        async with conn.transaction():
            for e in entries:
                uci_moves = e.uci_moves
                if not uci_moves:
                    continue
                try:
                    move_seq, board = _pretty_move_sequence(uci_moves)
                except (ValueError, AssertionError):
                    continue

                last_uci = uci_moves[-1]
                last_san = move_seq.split()[-1].split(".")[-1]

                await conn.execute(
                    """
                    INSERT INTO opening_nodes (
                        eco_code, opening_name, move_uci, move_san,
                        move_sequence, fen, depth
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (fen_canonical) DO UPDATE SET
                        eco_code     = EXCLUDED.eco_code,
                        opening_name = EXCLUDED.opening_name,
                        move_sequence= EXCLUDED.move_sequence,
                        updated_at   = NOW()
                    """,
                    e.eco,
                    e.name,
                    last_uci,
                    last_san,
                    move_seq,
                    board.fen(),
                    len(uci_moves),
                )
                n += 1
    return n


async def link_parents() -> int:
    """Walk every opening node's move sequence, look up the FEN after
    dropping the final move, and set ``parent_id`` to the matching node.
    Also builds an ltree ``path`` by slugifying opening_name.
    """
    import re

    def slug(s: str) -> str:
        return re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_") or "opening"

    rows = await db.fetch(
        "SELECT id, move_sequence, fen, opening_name FROM opening_nodes ORDER BY depth"
    )

    # canonical-fen -> id lookup
    fen_idx: dict[str, int] = {}
    for r in rows:
        canon = " ".join(r["fen"].split()[:4])
        fen_idx[canon] = r["id"]

    updates = 0
    async with db.acquire() as conn:
        async with conn.transaction():
            for r in rows:
                moves = r["move_sequence"].split()
                # strip off trailing move pair "N.e4" / "e5"
                # easier: recompute board from SAN list
                board = chess.Board()
                san_list: list[str] = []
                for token in moves:
                    # drop move-number prefixes like "1." or "12..."
                    if "." in token:
                        token = token.split(".")[-1]
                    if not token:
                        continue
                    san_list.append(token)

                if not san_list:
                    continue

                # play all but last
                try:
                    for san in san_list[:-1]:
                        board.push_san(san)
                except (ValueError, AssertionError):
                    continue

                parent_canon = " ".join(board.fen().split()[:4])
                parent_id = fen_idx.get(parent_canon)

                path_segments: list[str] = []
                cursor = r["opening_name"] or ""
                for seg in cursor.replace(":", ",").split(","):
                    path_segments.append(slug(seg.strip()))
                ltree_path = ".".join(s for s in path_segments if s) or slug(cursor)

                await conn.execute(
                    "UPDATE opening_nodes SET parent_id = $1, path = $2::ltree WHERE id = $3",
                    parent_id,
                    ltree_path,
                    r["id"],
                )
                updates += 1
    return updates
