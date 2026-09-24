import { Braces, Check, ListMusic, Palette } from "lucide-react";

import { StackRow } from "../components/StackRow";

const stack = [
  {
    icon: Braces,
    label: "React + TypeScript",
    detail: "Typed components, strict compiler settings",
  },
  {
    icon: Palette,
    label: "Tailwind CSS",
    detail: "Utility styles compiled by the Vite plugin",
  },
  {
    icon: ListMusic,
    label: "Lucide icons",
    detail: "One icon system, rendered from lucide-react",
  },
];

/**
 * Temporary shell for the new frontend. It exists to prove that the React,
 * TypeScript, Tailwind, and Lucide toolchain renders; the SpotM3U screens are
 * ported here in later tasks, while the Flask templates stay in use.
 */
export default function Foundation() {
  return (
    <div className="flex min-h-full items-center justify-center p-8">
      <section className="w-full max-w-md border border-edge bg-surface-raised">
        <header className="flex items-center gap-2 border-b border-edge px-4 py-2.5">
          <ListMusic aria-hidden className="size-4 text-ink-muted" />
          <h1 className="text-sm font-semibold">SpotM3U</h1>
          <span className="ml-auto inline-flex items-center gap-1 border border-edge px-1.5 py-0.5 text-[11px] text-ink-muted">
            <Check aria-hidden className="size-3" />
            React frontend
          </span>
        </header>

        <dl className="divide-y divide-edge">
          {stack.map((item) => (
            <StackRow key={item.label} {...item} />
          ))}
        </dl>

        <p className="border-t border-edge px-4 py-3 text-xs leading-relaxed text-ink-muted">
          Frontend foundation only. The Flask interface remains the one in use
          until the migrated screens land.
        </p>
      </section>
    </div>
  );
}
