import { useCallback, useEffect, useState } from "react";
import { Settings } from "lucide-react";
import { ApiError, clearStoredData, getStorageInventory } from "../lib/api";
import type { StorageInventory, StorageItemName } from "../lib/api";
import { AppDialog } from "./Dialog";
import { useToast } from "./Toast";

/**
 * The developer tools in the sidebar footer: a way to delete what SpotM3U wrote
 * without leaving the application and hunting folders by hand.
 *
 * The action opens a dialog shaped like a browser's "clear browsing data" one -
 * one checkbox per part of the library, each labelled with what it currently
 * holds, and a single action at the bottom - because the parts are independent
 * (a stale artwork cache is worth clearing on its own, a library of songs
 * usually is not) and a user should choose with the real counts in view. The
 * dialog stays open while the deletion runs and closes when it is done, so a
 * failure is reported without the choice being lost.
 *
 * Deleting is permanent, so the action stays disabled until the phrase the API
 * asked for has been typed.
 *
 * Only files SpotM3U wrote are ever in reach: the MP3s and playlists in the
 * download folder, its manifest, and the two cache folders inside it. The rest
 * of the music library is not offered here and is not deleted.
 */

const ITEMS: { name: StorageItemName; label: string }[] = [
  { name: "songs", label: "Downloaded songs" },
  { name: "playlists", label: "Generated playlists" },
  { name: "manifest", label: "Download record (re-download instead of reuse)" },
  { name: "artwork", label: "Artwork cache" },
  { name: "lyrics", label: "Lyrics cache (.lrc sidecars)" },
  { name: "uploads", label: "Upload working files" },
];

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

export function DeveloperTools() {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [inventory, setInventory] = useState<StorageInventory | null>(null);
  const [selected, setSelected] = useState<StorageItemName[]>([]);
  const [phrase, setPhrase] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setInventory(await getStorageInventory());
    } catch {
      // The counts are a convenience: the dialog still names the parts, and a
      // clear that cannot run reports its own failure.
      setInventory(null);
    }
  }, []);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const phraseRequired = inventory?.confirm_phrase ?? "";
  const canClear = selected.length > 0 && phrase === phraseRequired && phraseRequired !== "" && !busy;

  const toggle = (name: StorageItemName) => {
    setSelected((current) =>
      current.includes(name) ? current.filter((item) => item !== name) : [...current, name],
    );
  };

  const describes = (name: StorageItemName): string => {
    if (!inventory) return "";
    const item = inventory.items.find((entry) => entry.name === name);
    if (!item || item.files === 0) return "empty";
    const files = item.files === 1 ? "1 file" : `${item.files} files`;
    return `${files}, ${formatBytes(item.bytes)}`;
  };

  const clearSelected = async () => {
    setBusy(true);
    try {
      const report = await clearStoredData(selected, phraseRequired);
      const files = Object.values(report.removed).reduce((total, count) => total + (count ?? 0), 0);
      toast.success(
        files === 1 ? "Deleted 1 file" : `Deleted ${files} files`,
        `Freed ${formatBytes(report.bytes_freed)}`,
      );
      setOpen(false);
      setSelected([]);
      setPhrase("");
      await load();
    } catch (error) {
      toast.error(
        "Nothing was deleted",
        error instanceof ApiError ? error.message : "The request failed.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex w-full items-center justify-start gap-3 rounded-md px-3 py-2 text-md font-medium text-ink-muted transition-colors hover:bg-state-hover hover:text-ink active:bg-state-active active:text-ink"
      >
        <span className="inline-flex size-5 shrink-0 items-center justify-center" aria-hidden="true">
          <Settings className="size-4" />
        </span>
        <span className="text-left">Developer</span>
      </button>

      <AppDialog
        open={open}
        onResult={() => setOpen(false)}
        title="Clear downloaded data"
        summary="Deletes what SpotM3U wrote, and cannot be undone. Only files in the download folder are removed; the rest of your music library is untouched."
        confirmLabel="Clear data"
        confirmDisabled={!canClear}
        confirmPersistent
        onConfirm={() => void clearSelected()}
        cancelLabel="Cancel"
      >
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {ITEMS.map((item) => {
            const size = describes(item.name);
            return (
              <li key={item.name}>
                <label className="flex cursor-pointer items-start gap-3 text-md text-ink-muted">
                  <input
                    type="checkbox"
                    className="mt-1.5 shrink-0"
                    checked={selected.includes(item.name)}
                    onChange={() => toggle(item.name)}
                  />
                  <span className="flex-1">{item.label}</span>
                  {size && <span className="shrink-0 text-ink-faint tabular-nums">{size}</span>}
                </label>
              </li>
            );
          })}
        </ul>

        <label className="flex flex-col gap-2 text-md text-ink-muted">
          <span>
            Type <span className="font-semibold text-ink">"{phraseRequired || "delete"}"</span> to
            confirm
          </span>
          <input
            type="text"
            value={phrase}
            autoComplete="off"
            spellCheck={false}
            placeholder={phraseRequired || "delete"}
            // Enter must not submit the surrounding dialog form, which would
            // resolve it as a cancel and close the dialog mid-typing.
            onKeyDown={(event) => {
              if (event.key === "Enter") event.preventDefault();
            }}
            onChange={(event) => setPhrase(event.target.value)}
            className="min-h-[2.375rem] rounded-[0.375rem] border border-line bg-surface px-3 text-md text-ink"
          />
        </label>
      </AppDialog>
    </>
  );
}
