import { ChevronDown, ChevronRight } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";

/** The exact figures behind a designed view: closed by default, one click away, never removed. */
export function FiguresDisclosure({
  children,
  label = "figures",
  defaultOpen = false,
  name,
}: {
  children: ReactNode;
  label?: string;
  /** Open at first only where the table is the task (the Position's Table layout). */
  defaultOpen?: boolean;
  /** What it holds, where the page has several disclosures: becomes part of the button's accessible name. */
  name?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="flex flex-col gap-2">
      <div>
        <Button
          variant="outline"
          aria-expanded={open}
          aria-label={name ? `${open ? "Hide" : "Show"} ${label}: ${name}` : undefined}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          {open ? `Hide ${label}` : `Show ${label}`}
        </Button>
      </div>
      {open && children}
    </div>
  );
}
