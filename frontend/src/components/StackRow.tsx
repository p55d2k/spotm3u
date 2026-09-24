import type { LucideIcon } from "lucide-react";

type StackRowProps = {
  icon: LucideIcon;
  label: string;
  detail: string;
};

export function StackRow({ icon: Icon, label, detail }: StackRowProps) {
  return (
    <div className="flex items-center gap-3 px-4 py-2.5">
      <Icon aria-hidden className="size-4 shrink-0 text-ink-muted" />
      <dt className="text-sm">{label}</dt>
      <dd className="ml-auto text-right text-xs text-ink-muted">{detail}</dd>
    </div>
  );
}
