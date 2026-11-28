import { Link } from "@tanstack/react-router";
import { ArrowDown, ArrowUp, X } from "lucide-react";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { linkTarget } from "@/lib/due";
import { clearAnnouncement, signedDelta, useAnnouncement } from "@/lib/notices";

/** How long a toast stays before it clears itself (the Notice is kept in Recent changes). */
const TOAST_MS = 12_000;

/**
 * The toast for a Notice (ADR-0033 section 8): one transient surface, bottom of the screen, in a
 * polite live region that is always in the page so a screen reader announces what arrives. The
 * arrow and sign beside the change carry direction as well as colour; "Take a look" opens the view.
 */
export function NoticeToaster() {
  const notice = useAnnouncement();
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(clearAnnouncement, TOAST_MS);
    return () => clearTimeout(timer);
  }, [notice]);
  const target = notice ? linkTarget(notice.link_path) : null;
  const signed = notice ? signedDelta(notice) : null;
  const Arrow = notice?.direction === "down" ? ArrowDown : ArrowUp;
  return (
    <div
      aria-live="polite"
      aria-atomic="true"
      className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex justify-center px-4"
    >
      {notice && target && (
        <div
          data-testid="notice-toast"
          className="pointer-events-auto flex w-full max-w-md items-start gap-3 rounded-xl border border-border bg-card p-4 text-sm text-card-foreground shadow-lg"
        >
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <p className="font-medium">{notice.headline}</p>
            {notice.effect && <p className="text-muted-foreground">{notice.effect}</p>}
            {signed && (
              <p className="flex items-center gap-1 tabular-nums">
                <Arrow size={16} aria-hidden />
                <span>{signed}</span>
              </p>
            )}
            <Link to={target.to} search={target.search} onClick={clearAnnouncement} className="w-fit underline">
              Take a look
            </Link>
          </div>
          <Button variant="ghost" aria-label="Dismiss this message" onClick={clearAnnouncement}>
            <X size={16} aria-hidden />
          </Button>
        </div>
      )}
    </div>
  );
}
