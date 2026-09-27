import { AppShell } from "./components/AppShell";
import type { WorkflowStage } from "./components/Sidebar";
import { ToastProvider } from "./components/Toast";
import { TooltipProvider } from "./components/Tooltip";
import { parseRoute, useRoute } from "./lib/router";
import type { AppRoute } from "./lib/router";
import Import from "./pages/Import";
import Playlists from "./pages/Playlists";
import Processing from "./pages/Processing";
import BatchProcessing from "./pages/BatchProcessing";
import Result from "./pages/Result";
import BatchResult from "./pages/BatchResult";
import History from "./pages/History";
import HistoryRun from "./pages/HistoryRun";
import { useDocumentTitle } from "./hooks/useDocumentTitle";

/**
 * The application shell: the route decides which conversion screen renders and
 * which workflow step the sidebar should mark as current. Every page is keyed
 * by its route so navigating between two conversions of the same kind starts
 * a fresh page (state, polling timers) instead of reusing the previous one.
 */
export default function App() {
  useDocumentTitle("SpotM3U");
  return (
    <ToastProvider>
      <TooltipProvider>
        <AppShellContent />
      </TooltipProvider>
    </ToastProvider>
  );
}

function AppShellContent() {
  const route = parseRoute(useRoute());

  return (
    <AppShell currentStage={stageFor(route.name)}>
      <div key={routeKey(route)}>{pageFor(route)}</div>
    </AppShell>
  );
}

// The history pages are not a conversion, so no workflow step is current there.
function stageFor(name: AppRoute["name"]): WorkflowStage | undefined {
  switch (name) {
    case "import":
      return 1;
    case "playlists":
      return 2;
    case "processing":
    case "batch-processing":
      return 3;
    case "result":
    case "batch-result":
      return 4;
    case "history":
    case "history-run":
      return undefined;
  }
}

function routeKey(route: AppRoute): string {
  switch (route.name) {
    case "import":
      return "/";
    case "playlists":
      return `/jobs/${route.jobId}/playlists`;
    case "processing":
      return `/jobs/${route.jobId}/playlists/${route.playlistId}/processing`;
    case "result":
      return `/jobs/${route.jobId}/playlists/${route.playlistId}/result`;
    case "batch-processing":
      return `/jobs/${route.jobId}/processing`;
    case "batch-result":
      return `/jobs/${route.jobId}/result`;
    case "history":
      return "/history";
    case "history-run":
      return `/history/${route.runId}`;
  }
}

function pageFor(route: AppRoute) {
  switch (route.name) {
    case "import":
      return <Import />;
    case "playlists":
      return <Playlists jobId={route.jobId} />;
    case "processing":
      return <Processing jobId={route.jobId} playlistId={route.playlistId} />;
    case "result":
      return <Result jobId={route.jobId} playlistId={route.playlistId} fromBatch={route.fromBatch === true} />;
    case "batch-processing":
      return <BatchProcessing jobId={route.jobId} />;
    case "batch-result":
      return <BatchResult jobId={route.jobId} />;
    case "history":
      return <History />;
    case "history-run":
      return <HistoryRun runId={route.runId} />;
  }
}
