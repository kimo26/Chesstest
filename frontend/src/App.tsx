import { BrowserRouter, Routes, Route } from "react-router-dom";
import Nav from "./components/Nav";
import Dashboard from "./pages/Dashboard";
import OpeningsExplorer from "./pages/OpeningsExplorer";
import Practice from "./pages/Practice";
import Puzzles from "./pages/Puzzles";
import Flashcards from "./pages/Flashcards";
import Chat from "./pages/Chat";
import Insights from "./pages/Insights";
import Progress from "./pages/Progress";

export default function App() {
  return (
    <BrowserRouter>
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
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
