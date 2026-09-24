import Foundation from "./pages/Foundation";
import { AppShell } from "./components/AppShell";
import { ToastProvider } from "./components/Toast";
import { TooltipProvider } from "./components/Tooltip";
import { useDocumentTitle } from "./hooks/useDocumentTitle";

export default function App() {
  useDocumentTitle("SpotM3U");

  return (
    <ToastProvider>
      <TooltipProvider>
        <AppShell currentStage={1}>
          <Foundation />
        </AppShell>
      </TooltipProvider>
    </ToastProvider>
  );
}