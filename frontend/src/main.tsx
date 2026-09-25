import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { applyTheme, initialTheme } from "./hooks/useTheme";
import "./index.css";

// The theme is pushed onto <html> before the first paint so a fresh window
// never flashes the other theme. The stored preference is already in the
// document (Flask renders it into the shell), and this covers the browser copy
// and the OS setting for a page served without one.
applyTheme(initialTheme());

const container = document.getElementById("root");

if (container === null) {
  throw new Error("Root container #root is missing from index.html");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);