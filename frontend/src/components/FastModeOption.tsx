type FastModeOptionProps = {
  checked: boolean;
  onChange: (checked: boolean) => void;
  outputLabel: string;
};

export function FastModeOption({ checked, onChange, outputLabel }: FastModeOptionProps) {
  return (
    <label className="flex cursor-pointer gap-3 border-l-2 border-accent bg-surface-subtle px-4 py-3 hover:bg-state-selected">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-1"
      />
      <span className="text-md text-ink-muted">
        <strong className="text-ink">Fast mode</strong> — find audio and write {outputLabel} without
        source checks, audio checks or metadata (tags, artwork, lyrics). Downloads finish much
        faster, but matches can be less accurate and the MP3s stay plain.
      </span>
    </label>
  );
}
