import React, { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { PageContext } from "../types";

/**
 * Shared state for the persistent Coach widget.
 *
 * Every page calls ``setPageContext`` from a useEffect so the widget knows
 * what the user is currently doing. When the user types a question, the
 * widget forwards the latest ``pageContext`` to /api/chat so the agent can
 * resolve references like "why was that bad?" without the user restating
 * the position.
 */
export interface CoachContextValue {
  pageContext: PageContext | null;
  setPageContext: (ctx: PageContext | null) => void;
  fen: string | null;
  openWidget: () => void;
  isOpen: boolean;
  setIsOpen: (v: boolean) => void;
}

const Ctx = createContext<CoachContextValue | null>(null);

export function CoachProvider({ children }: { children: React.ReactNode }) {
  const [pageContext, setPageContextState] = useState<PageContext | null>(null);
  const [isOpen, setIsOpen] = useState<boolean>(() => {
    try {
      return localStorage.getItem("coach.open") === "1";
    } catch {
      return false;
    }
  });

  // Use a ref in setPageContext so repeated setState with equivalent objects
  // doesn't trigger re-renders of the widget.
  const lastSerialized = useRef<string>("");
  const setPageContext = useCallback((ctx: PageContext | null) => {
    const s = ctx ? JSON.stringify(ctx) : "";
    if (s === lastSerialized.current) return;
    lastSerialized.current = s;
    setPageContextState(ctx);
  }, []);

  const openWidget = useCallback(() => {
    setIsOpen(true);
    try {
      localStorage.setItem("coach.open", "1");
    } catch {}
  }, []);

  const toggleOpen = useCallback((v: boolean) => {
    setIsOpen(v);
    try {
      localStorage.setItem("coach.open", v ? "1" : "0");
    } catch {}
  }, []);

  const fen = useMemo(() => {
    if (pageContext && "fen" in pageContext && typeof pageContext.fen === "string") {
      return pageContext.fen;
    }
    return null;
  }, [pageContext]);

  const value = useMemo<CoachContextValue>(
    () => ({ pageContext, setPageContext, fen, openWidget, isOpen, setIsOpen: toggleOpen }),
    [pageContext, setPageContext, fen, openWidget, isOpen, toggleOpen]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useCoach(): CoachContextValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useCoach must be called inside <CoachProvider>");
  return v;
}

/**
 * Hook pages use to publish their context into the coach widget.
 *
 * Usage:
 *     useCoachPageContext({ page: "practice", fen, last_move: lastMove });
 *
 * The deps list is derived from the context object's serialisation, so
 * callers don't need to memoise by hand.
 */
export function useCoachPageContext(ctx: PageContext | null) {
  const { setPageContext } = useCoach();
  const serialized = ctx ? JSON.stringify(ctx) : "";
  React.useEffect(() => {
    setPageContext(ctx);
    return () => {
      // clear on unmount so a stale context doesn't leak across routes
      setPageContext(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serialized]);
}
