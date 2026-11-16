import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

/** Status badges. Same state, same badge, everywhere. */
export const badgeVariants = cva(
  "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap",
  {
    variants: {
      variant: {
        ok: "border-success/30 bg-success/10 text-success",
        info: "border-info/30 bg-info/10 text-info",
        flagged: "border-warning/40 bg-warning/10 text-warning",
        stale: "border-warning/40 bg-warning/10 text-warning",
        closed: "border-border bg-muted text-muted-foreground",
        superseded: "border-border bg-muted text-muted-foreground",
      },
    },
    defaultVariants: { variant: "info" },
  },
);

export function Badge({
  className,
  variant,
  ...props
}: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
