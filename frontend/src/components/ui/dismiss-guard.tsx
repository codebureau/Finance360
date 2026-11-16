import * as React from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

/**
 * What a Sheet or Dialog knows about unsaved input (docs/adr/0014: dismissing is Cancel, the X or
 * Esc, never a click outside). A form inside marks itself dirty; Cancel, the X and Esc then ask
 * before discarding instead of closing.
 */
export type DismissGuard = {
  dirty: boolean;
  setDirty: (dirty: boolean) => void;
  /** Ask "Discard your changes?". */
  ask: () => void;
};

const GuardContext = React.createContext<DismissGuard | null>(null);
export const DismissGuardProvider = GuardContext.Provider;

export function useDismissGuardState(): {
  guard: DismissGuard;
  confirming: boolean;
  setConfirming: (open: boolean) => void;
} {
  const [dirty, setDirty] = React.useState(false);
  const [confirming, setConfirming] = React.useState(false);
  const ask = React.useCallback(() => setConfirming(true), []);
  const guard = React.useMemo(() => ({ dirty, setDirty, ask }), [dirty, ask]);
  return { guard, confirming, setConfirming };
}

export function useDismissGuard(): DismissGuard | null {
  return React.useContext(GuardContext);
}

/**
 * Props to spread on the `<form>` inside a Sheet or Dialog: any edit (a field changing, or a
 * button other than submit, such as adding or removing a row) marks the form dirty. A form with
 * its own idea of "changed" can call `useUnsavedChanges` instead.
 */
export function useDirtyFormProps(): Pick<
  React.ComponentProps<"form">,
  "onChange" | "onClick"
> {
  const guard = useDismissGuard();
  return {
    onChange: () => guard?.setDirty(true),
    onClick: (e) => {
      if ((e.target as Element).closest('button[type="button"]'))
        guard?.setDirty(true);
    },
  };
}

/** Mark the surrounding Sheet or Dialog dirty while `dirty` is true. */
export function useUnsavedChanges(dirty: boolean): void {
  const guard = useDismissGuard();
  const setDirty = guard?.setDirty;
  React.useEffect(() => {
    if (dirty) setDirty?.(true);
  }, [dirty, setDirty]);
}

/** The click handler for a Close button: asks first, and does not close, while there is unsaved input. */
export function useGuardedCloseClick(
  onClick?: React.MouseEventHandler<HTMLButtonElement>,
): React.MouseEventHandler<HTMLButtonElement> {
  const guard = useDismissGuard();
  return (e) => {
    onClick?.(e);
    if (guard?.dirty && !e.defaultPrevented) {
      e.preventDefault();
      guard.ask();
    }
  };
}

/** "Discard your changes?": Keep editing, or Discard (which clicks the real Close). */
export function DiscardChanges({
  open,
  onOpenChange,
  Close,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  Close: React.ComponentType<{ asChild?: boolean; children: React.ReactNode }>;
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Discard your changes?</AlertDialogTitle>
          <AlertDialogDescription>
            What you have entered here has not been saved and will be lost.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Keep editing</AlertDialogCancel>
          <Close asChild>
            <AlertDialogAction>Discard</AlertDialogAction>
          </Close>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
