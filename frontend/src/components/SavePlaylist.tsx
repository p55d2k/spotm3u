import { useState } from "react";
import type { DragEvent } from "react";
import { AppDialog } from "./Dialog";
import { Button } from "./Button";
import { useToast } from "./Toast";
import { getBridge } from "../lib/bridge";
import { ApiError } from "../lib/api";

/**
 * "Save playlist (M3U)" with the application's one confirmation, migrated from
 * ``_save_m3u.html``. In the desktop shell the OS save panel is driven through
 * the window bridge and the outcome arrives as the shared toast (with an
 * "Open folder" action); in a browser run the attachment is downloaded through
 * a same-origin fetch instead. Either way a playlist whose files were deleted
 * by hand is refused with the "Save anyway" dialog before anything is written.
 */
export function SavePlaylistButton({
  jobId,
  playlistId,
  m3uUrl,
  downloadName,
}: {
  jobId: string;
  playlistId: string;
  /** The API's ``m3u_url`` (an absolute ``/api/...`` path). */
  m3uUrl: string;
  /** The browser fallback's file name, without extension. */
  downloadName: string;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  /** The pending confirmation: missing files the "Save anyway" dialog lists. */
  const [pending, setPending] = useState<{
    missing: string[];
    total: number;
    confirmUrl: string;
  } | null>(null);

  const fail = (detail: string) => {
    toast.error("The playlist could not be saved.", detail);
  };

  const browserDownload = async (url: string) => {
    const response = await fetch(url);
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as
        | { code?: string; missing?: string[]; total?: number; confirm_url?: string }
        | null;
      if (response.status === 409 && body?.confirm_url) {
        setPending({
          missing: body.missing ?? [],
          total: body.total ?? 0,
          confirmUrl: body.confirm_url,
        });
        return;
      }
      throw new ApiError(response.status, { error: body?.code ?? "", code: body?.code ?? "unknown" });
    }
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = `${downloadName}.m3u`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(objectUrl);
  };

  const save = async (confirm: boolean) => {
    const bridge = getBridge();
    setBusy(true);
    try {
      if (bridge) {
        const result = await bridge.save_m3u(jobId, playlistId, confirm);
        // A dismissed save panel is not a failure: the user chose not to save.
        if (result.cancelled) return;
        if (result.error) {
          fail(result.error);
          return;
        }
        if (result.confirm_required) {
          setPending({
            missing: result.names ?? [],
            total: result.total ?? 0,
            confirmUrl: "",
          });
          return;
        }
        toast.success(result.name ?? "Playlist", "Saved to Downloads.", {
          action: {
            label: "Open folder",
            onClick: () => {
              if (result.path) {
                bridge.open_at(result.path).catch(() => undefined);
              }
            },
          },
        });
      } else {
        await browserDownload(m3uUrl);
      }
    } catch (error) {
      if (error instanceof ApiError) {
        fail(error.message);
      } else {
        console.error("saving the playlist failed", error);
        fail("The playlist is still in the download folder.");
      }
    } finally {
      setBusy(false);
    }
  };

  const handleDrag = (event: DragEvent<HTMLElement>) => event.preventDefault();

  return (
    <>
      <Button busy={busy} onClick={() => void save(false)} onDragStart={handleDrag}>
        Save playlist (M3U)
      </Button>
      <AppDialog
        open={pending !== null}
        onResult={(result) => {
          if (pending && result === "confirm") {
            const confirmUrl = pending.confirmUrl;
            setPending(null);
            if (confirmUrl) {
              void browserDownload(confirmUrl).catch(() => fail("Try again in a moment."));
            } else {
              void save(true);
            }
          } else {
            setPending(null);
          }
        }}
        title={pending ? `${pending.missing.length} of ${pending.total} tracks are missing` : ""}
        summary="Saving writes a playlist that skips these tracks."
        groupLabel="Missing files"
        items={pending?.missing ?? []}
        confirmLabel="Save anyway"
      />
    </>
  );
}