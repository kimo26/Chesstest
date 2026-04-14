import { useEffect, useState } from "react";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import Nav from "./components/Nav";
import CoachWidget from "./components/CoachWidget";
import { CoachProvider } from "./context/CoachContext";
import { getOnboardingStatus } from "./api/client";
import Dashboard from "./pages/Dashboard";
import OpeningsExplorer from "./pages/OpeningsExplorer";
import Practice from "./pages/Practice";
import Puzzles from "./pages/Puzzles";
import Flashcards from "./pages/Flashcards";
import Chat from "./pages/Chat";
import Insights from "./pages/Insights";
import Progress from "./pages/Progress";
import Onboarding from "./pages/Onboarding";

/**
 * On boot we ask the backend whether this install has been onboarded. If
 * not, we force every route to /onboarding until the wizard completes,
 * so the user never lands on an empty Dashboard staring at zero games.
 */
function OnboardingGate({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const [checked, setChecked] = useState(false);
  const [needs, setNeeds] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getOnboardingStatus()
      .then((s) => {
        if (!cancelled) {
          setNeeds(!!s.needs_onboarding);
          setChecked(true);
        }
      })
      .catch(() => {
        // If the status endpoint isn't reachable, let the app render so
        // the user can at least see what's broken.
        if (!cancelled) setChecked(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!checked) {
    return <div className="app__booting">Starting Chess Coach…</div>;
  }
  if (needs && location.pathname !== "/onboarding") {
    return <Navigate to="/onboarding" replace />;
  }
  return <>{children}</>;
}

export default function App() {
  return (
    <BrowserRouter>
      <CoachProvider>
        <OnboardingGate>
          <div className="app">
            <Nav />
            <main className="main">
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/openings" element={<OpeningsExplorer />} />
                <Route path="/practice" element={<Practice />} />
                <Route path="/puzzles" element={<Puzzles />} />
                <Route path="/flashcards" element={<Flashcards />} />
                <Route path="/chat" element={<Chat />} />
                <Route path="/insights" element={<Insights />} />
                <Route path="/progress" element={<Progress />} />
                <Route path="/onboarding" element={<Onboarding />} />
              </Routes>
            </main>
            <CoachWidget />
          </div>
        </OnboardingGate>
      </CoachProvider>
    </BrowserRouter>
  );
}
