import { Navigate, Route, Routes } from "react-router-dom";
import { Suspense, lazy } from "react";
import { AppShell } from "./components/AppShell";
import { Skeleton } from "./components/ui/skeleton";

const RunsPage = lazy(() => import("./pages/RunsPage").then((m) => ({ default: m.RunsPage })));
const RunDetailPage = lazy(() => import("./pages/RunDetailPage").then((m) => ({ default: m.RunDetailPage })));
const FlowsPage = lazy(() => import("./pages/FlowsPage").then((m) => ({ default: m.FlowsPage })));
const FlowDetailPage = lazy(() => import("./pages/FlowDetailPage").then((m) => ({ default: m.FlowDetailPage })));
const DeploymentsPage = lazy(() =>
  import("./pages/DeploymentsPage").then((m) => ({ default: m.DeploymentsPage }))
);
const DeploymentDetailPage = lazy(() =>
  import("./pages/DeploymentDetailPage").then((m) => ({ default: m.DeploymentDetailPage }))
);
const WorkPoolsPage = lazy(() => import("./pages/WorkPoolsPage").then((m) => ({ default: m.WorkPoolsPage })));
const WorkPoolDetailPage = lazy(() =>
  import("./pages/WorkPoolDetailPage").then((m) => ({ default: m.WorkPoolDetailPage }))
);
const ConcurrencyPage = lazy(() =>
  import("./pages/ConcurrencyPage").then((m) => ({ default: m.ConcurrencyPage }))
);

function PageFallback() {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="Loading page">
      <Skeleton className="h-8 w-48" />
      <Skeleton className="h-4 w-96 max-w-full" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

export function App() {
  return (
    <AppShell>
      <Suspense fallback={<PageFallback />}>
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
          <Route path="/concurrency" element={<ConcurrencyPage />} />
        </Routes>
      </Suspense>
    </AppShell>
  );
}
