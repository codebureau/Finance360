import * as SheetPrimitive from "@radix-ui/react-dialog";
import { Maximize2, Minimize2, X } from "lucide-react";
import * as React from "react";

import {
  DiscardChanges,
  DismissGuardProvider,
  useDismissGuardState,
  useGuardedCloseClick,
} from "@/components/ui/dismiss-guard";
import { cn } from "@/lib/utils";

export const Sheet = SheetPrimitive.Root;
export const SheetTrigger = SheetPrimitive.Trigger;
export const SheetTitle = SheetPrimitive.Title;
export const SheetDescription = SheetPrimitive.Description;

/** Cancel inside a Sheet: closes, or asks "Discard your changes?" while the form has unsaved input. */
export function SheetClose({
  onClick,
  ...props
}: React.ComponentProps<typeof SheetPrimitive.Close>) {
  return (
    <SheetPrimitive.Close onClick={useGuardedCloseClick(onClick)} {...props} />
  );
}

const ExpandContext = React.createContext<((expand: boolean) => void) | null>(
  null,
);

/**
 * Ask the surrounding expandable Sheet to open at full width: it expands when `wanted` turns true,
 * and the person can still collapse it afterwards.
 */
export function useExpandSheet(wanted: boolean): void {
  const expand = React.useContext(ExpandContext);
  React.useEffect(() => {
    if (wanted) expand?.(true);
  }, [wanted, expand]);
}

/**
 * A drawer from the left or right edge, for longer forms and the mobile menu.
 *
 * Clicking or pointing outside never closes it (a click outside once lost a year of typing);
 * dismissing is Cancel, the X or Esc. While a form inside has unsaved input (see
 * `useDirtyFormProps`) those ask "Discard your changes?" first. `expandable` adds a button to
 * cover the full viewport width for sheet-like entry. `dismissOnOutsideClick` is for a drawer with
 * nothing to lose, such as the navigation menu.
 */
export function SheetContent({
  className,
  children,
  side = "right",
  expandable = false,
  dismissOnOutsideClick = false,
  onEscapeKeyDown,
  onPointerDownOutside,
  onInteractOutside,
  ...props
}: React.ComponentProps<typeof SheetPrimitive.Content> & {
  side?: "left" | "right";
  expandable?: boolean;
  dismissOnOutsideClick?: boolean;
}) {
  const { guard, confirming, setConfirming } = useDismissGuardState();
  const [expanded, setExpanded] = React.useState(false);
  return (
    <SheetPrimitive.Portal>
      <SheetPrimitive.Overlay className="fixed inset-0 z-50 bg-scrim" />
      <SheetPrimitive.Content
        className={cn(
          "fixed inset-y-0 z-50 flex w-[85vw] max-w-sm flex-col gap-4 overflow-y-auto border-border bg-popover p-6 text-popover-foreground shadow-lg",
          side === "right" ? "right-0 border-l" : "left-0 border-r",
          className,
          expandable && expanded && "w-full max-w-none sm:max-w-none",
          expandable && "[&>h2]:pr-16",
        )}
        data-expanded={expandable ? expanded : undefined}
        onEscapeKeyDown={(e) => {
          onEscapeKeyDown?.(e);
          if (!e.defaultPrevented && guard.dirty) {
            e.preventDefault();
            guard.ask();
          }
        }}
        onPointerDownOutside={(e) => {
          onPointerDownOutside?.(e);
          if (!dismissOnOutsideClick) e.preventDefault();
        }}
        onInteractOutside={(e) => {
          onInteractOutside?.(e);
          if (!dismissOnOutsideClick) e.preventDefault();
        }}
        {...props}
      >
        <DismissGuardProvider value={guard}>
          <ExpandContext.Provider value={expandable ? setExpanded : null}>
            {children}
          </ExpandContext.Provider>
        </DismissGuardProvider>
        {expandable && (
          <button
            type="button"
            aria-label={expanded ? "Collapse" : "Expand"}
            aria-pressed={expanded}
            onClick={() => setExpanded(!expanded)}
            className="absolute top-3 right-10 rounded-md p-1 text-muted-foreground hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            {expanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
          </button>
        )}
        <SheetPrimitive.Close
          aria-label="Close panel"
          onClick={(e) => {
            if (guard.dirty) {
              e.preventDefault();
              guard.ask();
            }
          }}
          className="absolute top-3 right-3 rounded-md p-1 text-muted-foreground hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          <X size={16} />
        </SheetPrimitive.Close>
        <DiscardChanges
          open={confirming}
          onOpenChange={setConfirming}
          Close={SheetPrimitive.Close}
        />
      </SheetPrimitive.Content>
    </SheetPrimitive.Portal>
  );
}
