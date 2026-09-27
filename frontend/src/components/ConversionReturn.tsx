import { ArrowLeft } from "lucide-react";
import { Button } from "./Button";
import { navigate } from "../lib/router";
import type { ConversionPlace } from "../lib/router";

/**
 * The way back to the conversion the user left, shown on the history screens.
 *
 * A conversion is addressed entirely by its URL, so the history is the one
 * place that can strand somebody: there is no sidebar step that leads back into
 * a running job and the import screen only takes a new ZIP. This carries the
 * last conversion screen and puts one honest label on it -- "Back to the
 * conversion in progress" rather than a generic "go back" -- so reading the
 * history stays a detour instead of becoming a dead end.
 */
export function ConversionReturn({ place }: { place: ConversionPlace }) {
  return (
    <Button variant="secondary" onClick={() => navigate(place.path)}>
      <ArrowLeft aria-hidden="true" className="size-4" />
      {place.label}
    </Button>
  );
}
