import { Link, Navigate, Route, Routes } from "react-router-dom";
import { FlowsPage } from "./pages/FlowsPage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { RunsPage } from "./pages/RunsPage";

export function App() {
  return (
    <div className="app">
      <header className="topbar">
        <h1>IronFlow</h1>
        <nav>
          <Link to="/runs">Runs</Link>
          <Link to="/flows">Flows</Link>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/runs" replace />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/runs/:id" element={<RunDetailPage />} />
          <Route path="/flows" element={<FlowsPage />} />
        </Routes>
      </main>
    </div>
  );
}
