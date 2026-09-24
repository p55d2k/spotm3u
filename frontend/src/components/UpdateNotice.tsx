import { Download } from "lucide-react";
import { useEffect, useState } from "react";
import { apiUrl } from "../lib/api";
import { getBridge } from "../lib/bridge";
import type { UpdateInfo } from "../lib/bridge";
import { useToast } from "./Toast";

const RELEASES_URL = "https://github.com/p55d2k/spotm3u/releases";

/**
 * The in-app update row that lives in the sidebar footer, migrated from
 * ``_update_notice.html``. Appears only when a newer SpotM3U release exists;
 * clicking it downloads the installer for this platform through the desktop
 * bridge (or opens the release page in a browser run), and the shared toast
 * carries the release notes and the folder the installer landed in.
 */
export function UpdateNotice() {
  const feedback = useToast();
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch(apiUrl("/update"))
      .then((response) => response.json())
      .then((data: UpdateInfo) => {
        if (!cancelled && data && data.update_available) setInfo(data);
      })
      .catch(() => {
        // An available update is nice to hear about but never worth an error.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!info) return null;

  const description = `Update to SpotM3U ${info.latest_version} (you are on ${info.current_version})`;

  const openPage = async (url: string) => {
    const api = getBridge();
    const target = url || RELEASES_URL;
    if (api && typeof api.open_url === "function") {
      try {
        const result = await api.open_url(target);
        if (!result || !result.error) return;
        console.error("opening the release page failed:", result.error);
      } catch (error) {
        console.error("opening the release page failed", error);
      }
    }
    window.open(target, "_blank", "noopener");
  };

  const download = async () => {
    const api = getBridge();
    if (!api || typeof api.download_update !== "function" || !info.asset_url) {
      // No download bridge (a browser run): the release page carries both the
      // notes and the installer, so it is the honest destination.
      window.open(info.release_url || RELEASES_URL, "_blank", "noopener");
      return;
    }
    setBusy(true);
    const result = await api.download_update(info.asset_url, info.asset_name ?? "");
    setBusy(false);
    if (!result || result.error) {
      feedback.error("The update could not be downloaded.", result ? result.error : "Try again in a moment.");
      return;
    }
    // The installer is never launched for the user: the toast spells out that
    // they should quit SpotM3U and open the file, with two useful next steps.
    feedback.success(result.name ?? "", "Downloaded to Downloads. Quit SpotM3U, then open it to install.", {
      actions: [
        { label: "Release notes", onClick: () => openPage(info.release_url ?? RELEASES_URL) },
        { label: "Open folder", onClick: () => api.open_at(result.path ?? "") },
      ],
      timeout: 7000,
    });
  };

  return (
    <button
      type="button"
      disabled={busy}
      onClick={() => download()}
      aria-label={description}
      title={description}
      className="inline-flex w-full items-center justify-start gap-3 rounded-md px-3 py-2 text-md font-medium text-ink-muted transition-colors hover:bg-state-hover hover:text-ink active:bg-state-active disabled:cursor-wait disabled:opacity-45"
    >
      <Download aria-hidden="true" className="size-4 shrink-0" />
      <span className="text-left">Update to {info.latest_version}</span>
    </button>
  );
}