import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import * as React from "react";

import {
  DiscardChanges,
  DismissGuardProvider,
  useDismissGuardState,
  useGuardedCloseClick,
} from "@/components/ui/dismiss-guard";
import { cn } from "@/lib/utils";

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;

/** Cancel inside a Dialog: closes, or asks "Discard your changes?" while the form has unsaved input. */
export function DialogClose({
  onClick,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Close>) {
  return (
    <DialogPrimitive.Close onClick={useGuardedCloseClick(onClick)} {...props} />
  );
}

export function DialogOverlay({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Overlay>) {
  return (
    <DialogPrimitive.Overlay
      className={cn("fixed inset-0 z-50 bg-scrim", className)}
      {...props}
    />
  );
}

/**
 * Centered modal for short forms. Radix returns focus to the trigger on close. Clicking or
 * pointing outside never closes it; dismissing is Cancel, the X or Esc, which ask "Discard your
 * changes?" first while a form inside has unsaved input (see `useDirtyFormProps`).
 */
export function DialogContent({
  className,
  children,
  onEscapeKeyDown,
  onPointerDownOutside,
  onInteractOutside,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Content>) {
  const { guard, confirming, setConfirming } = useDismissGuardState();
  return (
    <DialogPrimitive.Portal>
      <DialogOverlay />
      <DialogPrimitive.Content
        className={cn(
          "fixed top-1/2 left-1/2 z-50 flex max-h-[90dvh] w-[calc(100vw-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 flex-col gap-4 overflow-y-auto rounded-xl border border-border bg-popover p-6 text-popover-foreground shadow-lg",
          className,
        )}
        onEscapeKeyDown={(e) => {
          onEscapeKeyDown?.(e);
          if (!e.defaultPrevented && guard.dirty) {
            e.preventDefault();
            guard.ask();
          }
        }}
        onPointerDownOutside={(e) => {
          onPointerDownOutside?.(e);
          e.preventDefault();
        }}
        onInteractOutside={(e) => {
          onInteractOutside?.(e);
          e.preventDefault();
        }}
        {...props}
      >
        <DismissGuardProvider value={guard}>{children}</DismissGuardProvider>
        <DialogPrimitive.Close
          aria-label="Close dialog"
          onClick={(e) => {
            if (guard.dirty) {
              e.preventDefault();
              guard.ask();
            }
          }}
          className="absolute top-3 right-3 rounded-md p-1 text-muted-foreground hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          <X size={16} />
        </DialogPrimitive.Close>
        <DiscardChanges
          open={confirming}
          onOpenChange={setConfirming}
          Close={DialogPrimitive.Close}
        />
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

export function DialogHeader({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div className={cn("flex flex-col gap-1.5 pr-6", className)} {...props} />
  );
}

export function DialogFooter({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("flex flex-wrap justify-end gap-2", className)}
      {...props}
    />
  );
}

export function DialogTitle({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Title>) {
  return (
    <DialogPrimitive.Title
      className={cn("text-lg leading-none font-semibold", className)}
      {...props}
    />
  );
}

export function DialogDescription({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Description>) {
  return (
    <DialogPrimitive.Description
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  );
}
