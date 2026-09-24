import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, DragEvent, FormEvent } from "react";
import { Upload } from "lucide-react";
import { PageHeader } from "../components/PageHeader";
import { Button, buttonClasses } from "../components/Button";
import { FieldHelp } from "../components/Notice";
import { ApiError, uploadArchive, uploadPicked } from "../lib/api";
import { getBridge } from "../lib/bridge";
import { navigate } from "../lib/router";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

const IDLE_HINT = "ZIP file from Exportify · up to your configured size limit";

/**
 * The import screen, migrated from ``index.html``: the drag-and-drop upload
 * zone for the Exportify ZIP, the desktop shell's native file picker when the
 * window bridge is present, and the "Export your playlists" instructions.
 * A dropped or picked ZIP fills the zone but the "Import ZIP" action performs
 * the upload; only the OS file dialog imports straight away. A successful
 * import carries the shell to the playlist selection page.
 */
export default function Import() {
  useDocumentTitle("Spotify to M3U Converter");
  const inputRef = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const [busy, setBusyState] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [hint, setHint] = useState(IDLE_HINT);
  const [error, setError] = useState<string | null>(null);

  const showError = (message: string) => {
    setError(message);
    setHint(message);
  };

  const selectFile = (candidate: File | null): candidate is File => {
    setError(null);
    setHint(IDLE_HINT);
    if (!candidate) {
      setFile(null);
      setFileName(null);
      return false;
    }
    if (!candidate.name.toLowerCase().endsWith(".zip")) {
      setFile(null);
      setFileName(null);
      showError("That is not a ZIP file — choose the Exportify export.");
      return false;
    }
    setFile(candidate);
    setFileName(candidate.name);
    return true;
  };

  const setBusy = (value: boolean) => setBusyState(value);

  const importArchive = async (chosen: File) => {
    setBusy(true);
    try {
      const job = await uploadArchive(chosen);
      navigate(`/jobs/${job.job_id}/playlists`);
    } catch (cause) {
      setBusy(false);
      if (cause instanceof ApiError) {
        showError(cause.message);
      } else {
        console.error("importing the archive failed", cause);
        showError("That archive could not be imported. Please try again.");
      }
    }
  };

  const importPicked = async () => {
    const bridge = getBridge();
    if (!bridge) return;
    setError(null);
    setHint(IDLE_HINT);
    let picked;
    try {
      picked = await bridge.choose_zip();
    } catch (cause) {
      console.error("opening the file picker failed", cause);
      showError("The file picker could not be opened.");
      return;
    }
    if (!picked || picked.cancelled) return;
    if (picked.error) {
      showError(picked.error);
      return;
    }
    setFileName(picked.name ?? null);
    setBusy(true);
    try {
      const job = await uploadPicked();
      navigate(`/jobs/${job.job_id}/playlists`);
    } catch (cause) {
      setBusy(false);
      showError(cause instanceof ApiError ? cause.message : "That archive could not be imported.");
    }
  };

  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    selectFile(event.target.files?.[0] ?? null);
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (file) {
      void importArchive(file);
    }
  };

  // The whole import page takes the drop — the zone is only where that shows
  // up — so a ZIP dropped anywhere in the window is not ignored. dragenter and
  // dragleave also fire while moving between elements, so depth is tracked.
  useEffect(() => {
    const carriesFiles = (event: DragEvent) =>
      Array.from(event.dataTransfer?.types ?? []).includes("Files");
    const onEnter = (event: DragEvent) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      dragDepth.current += 1;
      setDragging(true);
    };
    const onOver = (event: DragEvent) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    };
    const onLeave = (event: DragEvent) => {
      if (!carriesFiles(event)) return;
      dragDepth.current = Math.max(0, dragDepth.current - 1);
      if (dragDepth.current === 0) setDragging(false);
    };
    const onDrop = (event: DragEvent) => {
      event.preventDefault();
      dragDepth.current = 0;
      setDragging(false);
      const dropped = event.dataTransfer?.files?.[0] ?? null;
      selectFile(dropped);
    };
    window.addEventListener("dragenter", onEnter as unknown as EventListener);
    window.addEventListener("dragover", onOver as unknown as EventListener);
    window.addEventListener("dragleave", onLeave as unknown as EventListener);
    window.addEventListener("drop", onDrop as unknown as EventListener);
    return () => {
      window.removeEventListener("dragenter", onEnter as unknown as EventListener);
      window.removeEventListener("dragover", onOver as unknown as EventListener);
      window.removeEventListener("dragleave", onLeave as unknown as EventListener);
      window.removeEventListener("drop", onDrop as unknown as EventListener);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const usesNativePicker = Boolean(getBridge());

  const zoneClass = `flex flex-col items-center gap-2 rounded-lg border-[1.5px] border-dashed p-10 text-center transition-[border-color,background-color] duration-[0.12s] mt-2 cursor-pointer ${
    error ? "border-danger" : "border-line-strong"
  } ${dragging ? "border-solid bg-accent-subtle border-accent" : ""} ${
    busy ? "cursor-progress opacity-75" : "hover:border-accent hover:bg-state-selected"
  }`;

  return (
    <>
      <PageHeader
        title="No playlist imported"
        intro="Import an Exportify ZIP to start matching your local music. SpotM3U reads only the ZIP you choose and never asks for your Spotify account."
      />
      <div className="grid gap-6 md:grid-cols-2">
        <form onSubmit={handleSubmit} className="flex flex-col rounded-lg border border-line bg-surface p-5">
          <h2 className="m-0 mb-4 text-base" id="upload-heading">
            Import your export
          </h2>
          <label
            htmlFor="export-file"
            className={zoneClass}
            onClick={(event) => {
              if (usesNativePicker) {
                event.preventDefault();
                void importPicked();
              }
            }}
          >
            <Upload aria-hidden="true" className="size-6 text-ink-faint" />
            <span className="text-base font-semibold text-ink">
              {busy ? "Reading your export…" : dragging ? "Drop to import" : "Drop your Exportify ZIP here"}
            </span>
            <span className="text-xs tracking-[0.08em] text-ink-faint uppercase">or</span>
            <span className={`${buttonClasses("secondary", "small")} pointer-events-none`}>Choose ZIP</span>
            <span className={`text-sm ${error ? "text-danger-ink" : "text-ink-muted"}`} role={error ? "alert" : undefined}>
              {fileName ?? hint}
            </span>
            <input
              ref={inputRef}
              id="export-file"
              name="file"
              type="file"
              accept=".zip,application/zip"
              aria-invalid={error ? "true" : undefined}
              className="sr-only"
              onChange={handleChange}
            />
          </label>
          <FieldHelp>Large playlists may take a while; keep this window open while SpotM3U works.</FieldHelp>
          <div className="mt-5">
            <Button type="submit" busy={busy}>
              {busy ? "Importing…" : "Import ZIP"}
            </Button>
          </div>
        </form>

        <section className="rounded-lg border border-line bg-surface p-5" aria-labelledby="instructions-heading">
          <h2 className="m-0 mb-4 text-base" id="instructions-heading">
            Export your playlists
          </h2>
          <ol className="m-0 flex list-none flex-col gap-4 p-0">
            <li className="flex flex-col items-start gap-3">
              <strong>Open Exportify</strong>
              <a
                className={`${buttonClasses("secondary", "small")} w-fit`}
                href="https://exportify.net/"
                target="_blank"
                rel="noopener noreferrer"
              >
                Open Exportify<span className="sr-only"> (opens in your browser)</span>
              </a>
            </li>
            <li><strong>Complete Spotify authentication, then press “Export All”</strong></li>
            <li><strong>Download the playlist export ZIP</strong>, then <strong>Import that ZIP here</strong></li>
          </ol>
        </section>
      </div>
    </>
  );
}