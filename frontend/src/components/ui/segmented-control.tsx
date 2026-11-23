import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export type SegmentOption<T extends string> = { value: T; label: string; icon: LucideIcon };

/**
 * A labelled radio group of icon buttons (ADR-0031 section 2): native radios, so the arrow keys
 * move the choice, it applies at once and focus stays on the control. Each option has an
 * accessible name and a tooltip of the same word.
 */
export function SegmentedControl<T extends string>({
  label,
  name,
  value,
  options,
  onChange,
}: {
  label: string;
  /** Groups the radios; unique per control on the page. */
  name: string;
  value: T;
  options: SegmentOption<T>[];
  onChange: (value: T) => void;
}) {
  return (
    <fieldset className="m-0 min-w-0 border-0 p-0">
      <legend className="sr-only">{label}</legend>
      <div className="inline-flex rounded-md border border-border bg-background p-0.5">
        {options.map(({ value: v, label: text, icon: Icon }) => (
          <label
            key={v}
            title={text}
            className={cn(
              "flex min-h-[calc(var(--control-h)-0.5rem)] min-w-9 cursor-pointer items-center justify-center rounded-sm px-2 text-muted-foreground transition-colors hover:text-foreground",
              "has-[:checked]:bg-accent has-[:checked]:text-accent-foreground has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring",
            )}
          >
            <input
              type="radio"
              name={name}
              value={v}
              checked={value === v}
              onChange={() => onChange(v)}
              className="sr-only"
              aria-label={text}
            />
            <Icon aria-hidden size={16} />
          </label>
        ))}
      </div>
    </fieldset>
  );
}
