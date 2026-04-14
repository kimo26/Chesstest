import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getOnboardingStatus, startOnboarding } from "../api/client";
import { useUser } from "../hooks/useUser";
import type { OnboardingStatus, OnboardingStep } from "../types";

/**
 * First-run onboarding wizard.
 *
 * App.tsx redirects here whenever /api/onboarding/status returns
 * ``needs_onboarding=true`` (fresh database, no user games). The user
 * enters their Chess.com username; we POST /api/onboarding/start to kick
 * off the pipelines, then poll /api/onboarding/status every 2s and render
 * a per-step progress bar + ETA until all steps report ``state: "ok"``.
 *
 * Every step is best-effort on the backend — individual failures append
 * an error to ``steps_done`` but don't abort the wizard, so the user can
 * still enter the app with a partially populated DB and re-run what
 * failed from the settings UI.
 */

function fmtSec(s: number | null | undefined): string {
  if (s == null) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s - m * 60);
  return rem > 0 ? `${m}m ${rem}s` : `${m}m`;
}

function stepIcon(state: OnboardingStep["state"]) {
  switch (state) {
    case "ok":
      return "✓";
    case "error":
      return "✗";
    case "running":
      return "⟳";
    default:
      return "•";
  }
}

export default function Onboarding() {
  const { user, setUser } = useUser();
  const navigate = useNavigate();
  const [username, setUsername] = useState<string>(user.chess_com_user || "");
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [started, setStarted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Poll status whenever the wizard is active.
  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const s = await getOnboardingStatus();
        if (cancelled) return;
        setStatus(s);
        if (!s.needs_onboarding && s.completed) {
          // Persist the resolved user id + chess.com username.
          if (s.user_id != null) {
            setUser({
              ...user,
              id: s.user_id,
              chess_com_user: s.chesscom_username || user.chess_com_user,
            });
          }
          navigate("/", { replace: true });
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    }
    tick();
    const iv = window.setInterval(tick, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(iv);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const begin = async () => {
    setError(null);
    try {
      const r = await startOnboarding({ chesscom_username: username.trim() });
      setStarted(true);
      setUser({ ...user, id: r.user_id, chess_com_user: username.trim() });
    } catch (e) {
      setError(String(e));
    }
  };

  const steps = status?.steps ?? [];
  const totalEta = steps
    .filter((s) => s.state !== "ok" && s.state !== "error")
    .reduce((acc, s) => acc + s.eta_seconds, 0);
  const anyRunning = steps.some((s) => s.state === "running");
  const anyComplete = status?.completed;

  return (
    <div className="onboarding">
      <div className="onboarding__card">
        <h1>Welcome to Chess Coach</h1>
        <p className="onboarding__lede">
          First-time setup: enter your Chess.com username and we'll pull your
          game history, analyse your opponents, load the opening tree, and
          build the knowledge base. Total time depends on how many games
          you've played.
        </p>

        {!started && !status?.in_progress && (
          <div className="onboarding__form">
            <label htmlFor="cc-user">Chess.com username</label>
            <input
              id="cc-user"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="e.g. hikaru"
              autoFocus
            />
            <button
              className="btn btn--primary"
              onClick={begin}
              disabled={!username.trim()}
            >
              Start
            </button>
            {error && <p className="onboarding__error">{error}</p>}
          </div>
        )}

        {(started || status?.in_progress) && (
          <div className="onboarding__progress">
            <div className="onboarding__heading-row">
              <h2>Preparing your coach…</h2>
              {anyRunning && !anyComplete && (
                <span className="onboarding__eta">
                  ≈ {fmtSec(totalEta)} remaining
                </span>
              )}
            </div>
            <ul className="onboarding__steps">
              {steps.map((s) => (
                <li
                  key={s.name}
                  className={`onboarding__step onboarding__step--${s.state}`}
                >
                  <span className="onboarding__step-icon">
                    {stepIcon(s.state)}
                  </span>
                  <span className="onboarding__step-label">{s.label}</span>
                  <span className="onboarding__step-meta">
                    {s.state === "ok"
                      ? `done in ${fmtSec(s.elapsed_seconds)}`
                      : s.state === "running"
                      ? `≈ ${fmtSec(s.eta_seconds)}`
                      : s.state === "error"
                      ? s.error ?? "error"
                      : `≈ ${fmtSec(s.eta_seconds)}`}
                  </span>
                  {s.detail && (
                    <span className="onboarding__step-detail">{s.detail}</span>
                  )}
                </li>
              ))}
            </ul>
            {anyComplete && (
              <p className="onboarding__done">
                All done! Redirecting to the dashboard…
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
