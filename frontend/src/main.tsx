import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { applyTheme, initialTheme } from "./hooks/useTheme";
import "./index.css";

// The theme is pushed onto <html> before the first paint so a fresh window
// never flashes the other theme, matching the Flask _theme_init.html script.
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