import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { DeploymentDetailPage } from "./pages/DeploymentDetailPage";
import { DeploymentsPage } from "./pages/DeploymentsPage";
import { FlowDetailPage } from "./pages/FlowDetailPage";
import { FlowsPage } from "./pages/FlowsPage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { RunsPage } from "./pages/RunsPage";
import { WorkPoolDetailPage } from "./pages/WorkPoolDetailPage";
import { WorkPoolsPage } from "./pages/WorkPoolsPage";

export function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/runs" replace />} />
        <Route path="/runs" element={<RunsPage />} />
        <Route path="/runs/:id" element={<RunDetailPage />} />
        <Route path="/flows" element={<FlowsPage />} />
        <Route path="/flows/:name" element={<FlowDetailPage />} />
        <Route path="/deployments" element={<DeploymentsPage />} />
        <Route path="/deployments/:id" element={<DeploymentDetailPage />} />
        <Route path="/work-pools" element={<WorkPoolsPage />} />
        <Route path="/work-pools/:id" element={<WorkPoolDetailPage />} />
      </Routes>
    </AppShell>
  );
}
