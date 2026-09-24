import { createContext, useCallback, useContext, useState } from "react";
import type { MouseEvent, ReactNode } from "react";

/**
 * The shared tooltip, migrated from ``_status_icons.html``: one element that
 * follows the pointer and shows whatever element in the app is hovered with a
 * ``data-tooltip``-style description. ``useTooltip()`` hands-out the mouse
 * handlers (and the text) that any element attaches, so no component in the
 * shell reimplements positioning.
 */

type TooltipState = { text: string; x: number; y: number; below: boolean } | null;

type TooltipApi = {
  bind(text: string): {
    onMouseOver(event: MouseEvent<HTMLElement>): void;
    onMouseOut(): void;
  };
};

const TooltipContext = createContext<TooltipApi | null>(null);

export function TooltipProvider({ children }: { children: ReactNode }) {
  const [tooltip, setTooltip] = useState<TooltipState>(null);

  const hide = useCallback(() => {
    setTooltip(null);
  }, []);

  const show = useCallback((text: string, rect: DOMRect) => {
    const below = rect.top < 140;
    setTooltip({
      text,
      x: rect.left + rect.width / 2,
      y: below ? rect.bottom : rect.top,
      below,
    });
  }, []);

  const api: TooltipApi = {
    bind(text) {
      return {
        onMouseOver: (event) => show(text, event.currentTarget.getBoundingClientRect()),
        onMouseOut: hide,
      };
    },
  };

  return (
    <TooltipContext.Provider value={api}>
      {children}
      {tooltip && (
        <div
          role="tooltip"
          className="pointer-events-none fixed left-0 top-0 z-[400] whitespace-nowrap rounded-[0.375rem] bg-ink px-2 py-1 text-xs font-medium text-bg"
          style={{
            left: tooltip.x,
            top: tooltip.y,
            transform: `translateX(-50%) translateY(${tooltip.below ? "-" : ""}0.375rem)`,
          }}
        >
          {tooltip.text}
        </div>
      )}
    </TooltipContext.Provider>
  );
}

export function useTooltip(): TooltipApi {
  const context = useContext(TooltipContext);
  if (!context) {
    throw new Error("useTooltip must be used inside a TooltipProvider");
  }
  return context;
}