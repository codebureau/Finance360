import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowDown, ArrowUp, X } from "lucide-react";

import { GeneralError } from "@/components/form-fields";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { ago } from "@/lib/backup";
import { linkTarget } from "@/lib/due";
import {
  ALL_NOTICES,
  dismissNotice,
  fetchNotices,
  HOME_NOTICES,
  NOTICES_KEY,
  signedDelta,
  type Notice,
} from "@/lib/notices";
import { cn } from "@/lib/utils";

/** Home shows the last ten undismissed (ADR-0033 section 6); the page lists more. */
const HOME_LIMIT = 10;
const PAGE_LIMIT = 50;

/** One Notice: headline, what it unlocked, the change as arrow and sign, when, a link and dismiss. */
function NoticeRow({ notice }: { notice: Notice }) {
  const queryClient = useQueryClient();
  const dismiss = useMutation({
    mutationFn: () => dismissNotice(notice.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: NOTICES_KEY }),
  });
  const target = linkTarget(notice.link_path);
  const signed = signedDelta(notice);
  const Arrow = notice.direction === "down" ? ArrowDown : ArrowUp;
  return (
    <li
      data-testid="notice-row"
      data-dismissed={notice.dismissed || undefined}
      className={cn("flex items-start gap-3 py-3", notice.dismissed && "text-muted-foreground")}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-1 text-sm">
        <p className="font-medium">{notice.headline}</p>
        {notice.effect && <p className="text-muted-foreground">{notice.effect}</p>}
        {signed && (
          <p className="flex items-center gap-1 tabular-nums">
            <Arrow size={16} aria-hidden />
            <span>{signed}</span>
          </p>
        )}
        <div className="flex flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
          <time dateTime={`${notice.created_at}Z`}>{ago(notice.created_at)}</time>
          {notice.dismissed && <span>Dismissed</span>}
          <Link to={target.to} search={target.search} className="text-sm underline">
            Take a look
          </Link>
        </div>
        <GeneralError error={dismiss.error} />
      </div>
      {!notice.dismissed && (
        <Button
          variant="ghost"
         
          aria-label={`Dismiss: ${notice.headline}`}
          onClick={() => dismiss.mutate()}
          disabled={dismiss.isPending}
        >
          <X size={16} aria-hidden />
        </Button>
      )}
    </li>
  );
}

function NoticeList({ notices }: { notices: Notice[] }) {
  return (
    <ul className="divide-y divide-border">
      {notices.map((n) => (
        <NoticeRow key={n.id} notice={n} />
      ))}
    </ul>
  );
}

/**
 * Card 3 of Home (view key `home.recent-changes`, shape `collection`, list form only, ADR-0033
 * section 8): the last ten undismissed Notices, newest first. A Notice has no card grid, so there is
 * no Cards and List switch.
 */
export function RecentChangesCard() {
  const query = useQuery({
    queryKey: HOME_NOTICES,
    queryFn: () => fetchNotices(false, HOME_LIMIT),
    refetchOnMount: "always",
  });
  const items = query.data;
  return (
    <section aria-labelledby="recent-changes" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="recent-changes" className="text-xl font-semibold">
          Recent changes
        </h2>
        <Link to="/recent-changes" className="text-sm underline">
          See all
        </Link>
      </div>
      {query.isPending && <p className="text-sm text-muted-foreground">Loading...</p>}
      <GeneralError error={query.error} />
      {items && items.length === 0 && <p className="text-sm text-muted-foreground">Nothing has changed lately.</p>}
      {items && items.length > 0 && <NoticeList notices={items} />}
    </section>
  );
}

/** `/recent-changes`: the same list, dismissed Notices muted. */
export function RecentChangesPage() {
  const query = useQuery({
    queryKey: ALL_NOTICES,
    queryFn: () => fetchNotices(true, PAGE_LIMIT),
    refetchOnMount: "always",
  });
  const items = query.data;
  return (
    <div className="flex max-w-3xl flex-col gap-4">
      <PageHeader
        title="Recent changes"
        description={
          <>
            What you added lately that moved the picture, kept for 30 days.{" "}
            <Link to="/guide/$slug" params={{ slug: "recent-changes" }} className="underline">
              About Recent changes
            </Link>
          </>
        }
      />
      {query.isPending && <p className="text-sm text-muted-foreground">Loading...</p>}
      <GeneralError error={query.error} />
      {items && items.length === 0 && <p className="text-sm text-muted-foreground">Nothing has changed lately.</p>}
      {items && items.length > 0 && <NoticeList notices={items} />}
    </div>
  );
}
