import { ChevronDown, ChevronRight } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";

/** The exact figures behind a designed view: closed by default, one click away, never removed. */
export function FiguresDisclosure({ children, label = "figures" }: { children: ReactNode; label?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-col gap-2">
      <div>
        <Button variant="outline" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          {open ? `Hide ${label}` : `Show ${label}`}
        </Button>
      </div>
      {open && children}
    </div>
  );
}
