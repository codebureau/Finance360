import { cn } from "@/lib/utils";

/**
 * The Finance360 mark (Open ring): a ring for the whole picture, with a rising line leaving through
 * its gap for the projection. Drawn on a 32px grid so the 16px favicon lands on whole pixels;
 * `public/favicon.svg` is the same drawing with literal colours. Decorative: the name beside it
 * carries the meaning.
 */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden focusable="false" className={cn("size-6 shrink-0", className)}>
      <rect width="32" height="32" rx="7" className="fill-brand" />
      <g fill="none" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className="stroke-brand-foreground">
        <path d="M25.4 12.58 A10 10 0 1 1 19.42 6.6" />
        <path d="M9.5 20.5 L14 16 L17.5 18.5 L26 10" />
      </g>
    </svg>
  );
}

/** The mark and the name, "Finance" in the text colour and "360" in brand blue. Reads as "Finance360". */
export function Logo({ className, markClassName }: { className?: string; markClassName?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <LogoMark className={markClassName} />
      {/* Split spans can be read as "Finance 360", so assistive tech gets the name as one word. */}
      <span className="sr-only">Finance360</span>
      <span aria-hidden>
        Finance<span className="text-brand-text">360</span>
      </span>
    </span>
  );
}
