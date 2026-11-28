import { useQuery } from "@tanstack/react-query";
import { Link, useRouterState } from "@tanstack/react-router";
import { ArrowLeft, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { FROM_PARAM } from "@/lib/due";
import { fetchQuestion, questionKey, questionOfMarker } from "@/lib/questions";
import { markerPath, parseMarker, readMarker, writeMarker } from "@/lib/return-bar";
import { cn } from "@/lib/utils";

/** What a Question return bar says: the Question, and the next thing it needs. It refetches as the
 * owner moves about inside the fix, so "next" is never the step they have just done. */
function NextStepText({ questionKey: key }: { questionKey: string }) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const query = useQuery({ queryKey: questionKey(key), queryFn: () => fetchQuestion(key), refetchOnMount: "always" });
  const { refetch } = query;
  useEffect(() => {
    void refetch();
  }, [pathname, refetch]);
  const q = query.data;
  if (!q) return null;
  return (
    <span className="min-w-0 text-sm break-words" data-testid="return-next">
      <span className="font-medium">{q.wording}</span>{" "}
      <span className="text-muted-foreground">
        {q.next_step ? `Next: ${q.next_step.title}` : "Nothing more is needed for now."}
      </span>
    </span>
  );
}

/**
 * The one return bar (ADR-0032 section 3): "Back to What's due" or "Back to your next step" on
 * whatever screen a fix link opened. The marker comes from the URL (`?from=`) and is kept per
 * browser tab, so the bar stays until the owner goes back, dismisses it, or follows another return
 * link. It lives in the shell so every current and future fix link gets it with no per-screen code.
 */
export function ReturnBar() {
  const { pathname, from } = useRouterState({
    select: (s) => ({
      pathname: s.location.pathname,
      from: parseMarker((s.location.search as Record<string, unknown>)[FROM_PARAM]),
    }),
    structuralSharing: true,
  });
  // A dismissal hides the bar here, even while the URL still carries the marker.
  const [dismissed, setDismissed] = useState<{ marker: string; pathname: string } | null>(null);
  const marker = from ?? readMarker();
  const target = marker ? markerPath(marker) : null;
  // Arriving is remembering; reaching the place the bar leads to is going back.
  useEffect(() => {
    const current = from ?? readMarker();
    if (!current) return;
    writeMarker(markerPath(current) === pathname ? undefined : current);
  }, [from, pathname]);
  if (!marker || !target || target === pathname) return null;
  if (dismissed?.marker === marker && dismissed.pathname === pathname) return null;
  const question = questionOfMarker(marker);
  const done = () => {
    writeMarker(undefined);
    setDismissed({ marker, pathname });
  };
  return (
    <div role="region" aria-label="Return" className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2">
      <Link to={target} onClick={done} className={cn(buttonVariants({ variant: "outline" }), "w-fit")}>
        <ArrowLeft size={16} aria-hidden />
        {question ? "Back to your next step" : "Back to What's due"}
      </Link>
      {question && <NextStepText questionKey={question} />}
      <Button variant="ghost" onClick={done} aria-label="Dismiss" className="ml-auto h-auto p-1 text-muted-foreground">
        <X size={16} aria-hidden />
      </Button>
    </div>
  );
}
