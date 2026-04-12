import { useEffect, useRef, useCallback } from "react";
import { Chessground } from "chessground";
import type { Api } from "chessground/api";
import type { Config } from "chessground/config";
import type { Key, Color } from "chessground/types";
import { Chess, type Square } from "chess.js";

interface Props {
  fen?: string;
  orientation?: Color;
  interactive?: boolean;
  onMove?: (from: string, to: string, promotion?: string) => void;
  lastMove?: [Key, Key];
  viewOnly?: boolean;
  highlight?: Key[];
  width?: number;
  height?: number;
}

export default function Chessboard({
  fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  orientation = "white",
  interactive = true,
  onMove,
  lastMove,
  viewOnly = false,
  highlight,
  width = 480,
  height = 480,
}: Props) {
  const boardRef = useRef<HTMLDivElement>(null);
  const apiRef = useRef<Api | null>(null);
  const chessRef = useRef(new Chess(fen));

  const toDests = useCallback((): Map<Key, Key[]> => {
    const dests = new Map<Key, Key[]>();
    if (viewOnly) return dests;
    const chess = chessRef.current;
    for (const move of chess.moves({ verbose: true })) {
      const from = move.from as Key;
      const existing = dests.get(from) || [];
      existing.push(move.to as Key);
      dests.set(from, existing);
    }
    return dests;
  }, [viewOnly]);

  useEffect(() => {
    if (!boardRef.current) return;
    const config: Config = {
      fen,
      orientation,
      turnColor: fen.includes(" w ") ? "white" : "black",
      movable: {
        free: false,
        color: interactive && !viewOnly ? orientation : undefined,
        dests: toDests(),
      },
      lastMove,
      highlight: { lastMove: true, check: true },
      animation: { enabled: true, duration: 150 },
      draggable: { enabled: interactive && !viewOnly },
      events: {
        move: (orig, dest) => {
          const chess = chessRef.current;
          // Detect promotion
          const piece = chess.get(orig as Square);
          let promotion: string | undefined;
          if (piece?.type === "p") {
            const rank = dest[1];
            if (rank === "8" || rank === "1") promotion = "q";
          }
          const result = chess.move({ from: orig, to: dest, promotion });
          if (!result) return;
          apiRef.current?.set({
            fen: chess.fen(),
            turnColor: chess.turn() === "w" ? "white" : "black",
            movable: { dests: toDests() },
            lastMove: [orig as Key, dest as Key],
          });
          onMove?.(orig, dest, promotion);
        },
      },
    };

    if (apiRef.current) {
      apiRef.current.set(config);
    } else {
      apiRef.current = Chessground(boardRef.current, config);
    }
  }, [fen, orientation, interactive, viewOnly, lastMove, onMove, toDests]);

  // Keep internal chess in sync when fen changes externally
  useEffect(() => {
    chessRef.current.load(fen);
    apiRef.current?.set({
      fen,
      turnColor: fen.includes(" w ") ? "white" : "black",
      movable: { dests: toDests() },
    });
  }, [fen, toDests]);

  useEffect(() => {
    return () => {
      apiRef.current?.destroy();
      apiRef.current = null;
    };
  }, []);

  return (
    <div
      ref={boardRef}
      className="chessboard-wrap"
      style={{ width, height }}
    />
  );
}
