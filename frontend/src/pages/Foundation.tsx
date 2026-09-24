import { Braces, ListMusic, Palette } from "lucide-react";

import { StackRow } from "../components/StackRow";
import { PageHeader } from "../components/PageHeader";
import { Panel } from "../components/Panel";

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
 * Placeholder screen rendered inside the completed application shell. It shows
 * the toolchain the shell is built on; the SpotM3U screens themselves are
 * ported in task 107, while the Flask templates stay in use.
 */
export default function Foundation() {
  return (
    <>
      <PageHeader
        title="No playlist imported"
        intro="Import an Exportify ZIP to start matching your local music. SpotM3U reads only the ZIP you choose and never asks for your Spotify account."
      />
      <Panel className="p-5">
        <h2 className="m-0 mb-4 text-base">Frontend foundation</h2>
        <dl className="m-0 divide-y divide-line">
          {stack.map((item) => (
            <StackRow key={item.label} {...item} />
          ))}
        </dl>
        <p className="m-0 mt-3 border-t border-line px-1 pt-3 text-xs leading-relaxed text-ink-muted">
          The application shell is in place and the Flask templates remain
          available until the migrated screens land.
        </p>
      </Panel>
    </>
  );
}