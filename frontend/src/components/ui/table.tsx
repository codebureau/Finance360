import * as React from "react";

import { cn } from "@/lib/utils";

/** A plain styled table. Row height and cell padding come from the density variables. */
export function Table({ className, ...props }: React.ComponentProps<"table">) {
  return (
    <div className="w-full overflow-x-auto rounded-lg border border-border">
      <table className={cn("w-full border-collapse text-sm", className)} {...props} />
    </div>
  );
}

export function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return <thead className={cn("bg-muted/50 text-left text-muted-foreground", className)} {...props} />;
}

export function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return <tbody className={cn("divide-y divide-border", className)} {...props} />;
}

export function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  return <tr className={cn("h-[var(--row-h)] hover:bg-accent/40", className)} {...props} />;
}

export function TableHead({ className, ...props }: React.ComponentProps<"th">) {
  return (
    <th
      className={cn("px-[var(--cell-px)] py-[var(--cell-py)] text-xs font-medium whitespace-nowrap", className)}
      {...props}
    />
  );
}

export function TableCell({ className, ...props }: React.ComponentProps<"td">) {
  return <td className={cn("px-[var(--cell-px)] py-[var(--cell-py)] align-middle", className)} {...props} />;
}
