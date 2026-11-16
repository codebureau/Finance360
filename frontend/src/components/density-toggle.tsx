import { useState } from "react";

import { Button } from "@/components/ui/button";
import { readDensity, saveDensity, type Density } from "@/lib/density";

const OPTIONS: { value: Density; label: string }[] = [
  { value: "normal", label: "Normal" },
  { value: "compact", label: "Compact" },
];

/** Table row height and control size: Normal or Compact (`data-density` on `<html>`). */
export function DensityToggle() {
  const [density, setDensity] = useState<Density>(readDensity);
  const choose = (next: Density) => {
    saveDensity(next);
    setDensity(next);
  };
  return (
    <div role="group" aria-label="Density" className="flex gap-2">
      {OPTIONS.map((o) => (
        <Button
          key={o.value}
          variant={density === o.value ? "secondary" : "outline"}
          aria-pressed={density === o.value}
          onClick={() => choose(o.value)}
        >
          {o.label}
        </Button>
      ))}
    </div>
  );
}
