import { ArrowLeftRight, Check, CircleCheck, ListMusic, Moon, Sun, Upload } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useShell } from "./Shell";
import { UpdateNotice } from "./UpdateNotice";
import { useTheme } from "../hooks/useTheme";

/**
 * The application navigation, migrated from ``_sidebar.html``: the brand, the
 * four conversion steps with one leading icon and a dot that carries workflow
 * state (position, or a check once the step is done), and a footer holding the
 * update notice and the theme toggle. On macOS the native traffic lights float
 * over the sidebar, so the brand is kept clear of them.
 */

export type WorkflowStage = 1 | 2 | 3 | 4;

type SidebarStep = {
  name: string;
  number: WorkflowStage;
  icon: LucideIcon;
  href?: string;
};

const STEPS: SidebarStep[] = [
  { name: "Import", number: 1, icon: Upload, href: "/app/" },
  { name: "Choose playlists", number: 2, icon: ListMusic },
  { name: "Convert", number: 3, icon: ArrowLeftRight },
  { name: "Done", number: 4, icon: CircleCheck },
];

export function Sidebar({ currentStage = 1 }: { currentStage?: WorkflowStage }) {
  const { framed, platform, maximized } = useShell();
  const { theme, toggle } = useTheme();

  const trafficGap =
    framed && platform === "mac" && !maximized ? "var(--spot-traffic-gap)" : undefined;

  return (
    <aside
      className="relative z-20 flex w-[var(--spot-sidebar-w)] flex-none flex-col overflow-y-auto bg-sidebar border-r border-line app-scroll"
      aria-label="Application navigation"
    >
      <div
        className={`px-4 ${trafficGap ? "" : framed && platform === "mac" ? "pb-5 pt-5" : "pb-4 pt-5"}`}
        style={trafficGap ? { paddingTop: trafficGap } : undefined}
      >
        <a href="/app/" aria-label="SpotM3U home" className="inline-flex items-center gap-3 text-ink no-underline">
          <img src="/api/icon.png" alt="" width="28" height="28" className="block size-7 flex-none rounded-[0.375rem]" />
          <span className="text-base font-bold tracking-[-0.02em]">SpotM3U</span>
        </a>
      </div>

      <div className="flex-1 border-t border-line px-3 py-4">
        <p className="m-0 mb-3 px-3 text-xs font-semibold tracking-[0.08em] text-ink-faint uppercase">
          Conversion
        </p>
        <ol className="m-0 flex list-none flex-col gap-1">
          {STEPS.map((step) => {
            const state =
              step.number === currentStage
                ? "current"
                : step.number < currentStage
                  ? "complete"
                  : "";
            const StepIcon = step.icon;
            const iconClass = `size-4 shrink-0 ${
              state === "complete"
                ? "text-success-ink"
                : state === "current"
                  ? "text-accent"
                  : "text-ink-faint"
            }`;
            const dotClass = `inline-flex size-[1.4rem] shrink-0 items-center justify-center rounded-full border text-xs font-semibold ${
              state === "complete"
                ? "border-transparent bg-success-subtle text-success-ink"
                : state === "current"
                  ? "border-transparent bg-accent text-on-accent"
                  : "border-line-strong text-ink-faint"
            }`;
            const linkClass = `flex items-center gap-3 rounded-md px-3 py-2 text-md font-medium text-ink-muted ${
              state === "current" ? "text-ink font-semibold" : ""
            } ${step.href ? "hover:bg-state-hover hover:text-ink hover:no-underline" : "cursor-default"}`;
            const content = (
              <>
                <StepIcon aria-hidden="true" className={iconClass} />
                <span aria-hidden="true" className={dotClass}>
                  {state === "complete" ? <Check aria-hidden="true" className="size-3" /> : step.number}
                </span>
                <span className="step-name">{step.name}</span>
              </>
            );
            return (
              <li
                key={step.name}
                className={`relative rounded-md ${state === "current" ? "bg-state-selected" : ""}`}
              >
                {step.href ? (
                  <a href={step.href} className={linkClass} aria-current={state === "current" ? "step" : undefined}>
                    {content}
                  </a>
                ) : (
                  <span className={linkClass} aria-current={state === "current" ? "step" : undefined}>
                    {content}
                  </span>
                )}
              </li>
            );
          })}
        </ol>
      </div>

      <div className="flex flex-col gap-1 border-t border-line p-3">
        <UpdateNotice />
        <button
          type="button"
          data-theme-toggle
          aria-pressed={theme === "dark" ? "true" : "false"}
          onClick={toggle}
          className="inline-flex w-full items-center justify-start gap-3 rounded-md px-3 py-2 text-md font-medium text-ink-muted transition-colors hover:bg-state-hover hover:text-ink active:bg-state-active active:text-ink"
        >
          <span className="inline-flex size-5 shrink-0 items-center justify-center" aria-hidden="true">
            {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </span>
          <span className="text-left" data-theme-label>
            {theme === "dark" ? "Light mode" : "Dark mode"}
          </span>
        </button>
      </div>
    </aside>
  );
}