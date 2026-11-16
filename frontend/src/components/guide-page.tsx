import { Link } from "@tanstack/react-router";
import { ArrowDown, ArrowRight } from "lucide-react";

import { Markdown } from "@/components/markdown";
import { buttonVariants } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { cn } from "@/lib/utils";
import { findGuidePage, GUIDE_PAGES, guidePath, START_SLUG } from "@/lib/guide";

/** What you provide, what is worked out from it, and the question it answers. One row each way in. */
const FLOWS: { provide: string; works: string; answers: string; later?: boolean }[] = [
  {
    provide: "Accounts and their Valuations",
    works: "Position: what you own and owe, by Member and Category, today and over time",
    answers: "Where do we stand?",
  },
  {
    provide: "Vanguard PI files, plus Prices and FX rates",
    works: "The value of each PI Account, from its Holdings, which feeds the Position",
    answers: "Where do we stand?",
  },
  {
    provide: "Income streams, Assumptions, the Position",
    works: "Projection: year by year, per Scenario",
    answers: "Are we on track?",
  },
  {
    provide: "Tax records and income statements, plus the Rule pack's figures",
    works: "Label totals, then the Tax estimate and the Income reconciliation",
    answers: "What is our tax this year, and where does our pay go?",
  },
  {
    provide: "Tax estimate, take-home pay",
    works: "Take-home pay passed to the Projection",
    answers: "Coming later. Today you type take-home pay into the Income stream.",
    later: true,
  },
];

/** The one diagram on "How the pieces fit": real text in a list, so it works in light and dark and on a phone. */
function PiecesDiagram() {
  return (
    <figure aria-label="How the pieces fit" className="flex flex-col gap-3">
      <div
        aria-hidden
        className="hidden grid-cols-[1fr_auto_1fr_auto_1fr] gap-2 text-xs font-medium tracking-wide text-muted-foreground uppercase md:grid"
      >
        <span>You provide</span>
        <span />
        <span>Finance360 works out</span>
        <span />
        <span>It answers</span>
      </div>
      <ol className="flex flex-col gap-4 md:gap-3">
        {FLOWS.map((f) => (
          <li
            key={f.provide}
            className={cn(
              "grid grid-cols-1 items-stretch gap-1 md:grid-cols-[1fr_auto_1fr_auto_1fr] md:items-center md:gap-2",
              f.later && "opacity-80",
            )}
          >
            <Box label="You provide">{f.provide}</Box>
            <Arrow />
            <Box label="Finance360 works out">{f.works}</Box>
            <Arrow />
            <Box label="It answers" strong={!f.later} dashed={f.later}>
              {f.answers}
            </Box>
          </li>
        ))}
      </ol>
    </figure>
  );
}

function Box({
  label,
  children,
  strong,
  dashed,
}: {
  label: string;
  children: React.ReactNode;
  strong?: boolean;
  dashed?: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border bg-card p-3 text-sm text-card-foreground",
        strong && "border-primary/50 font-medium",
        dashed && "border-dashed",
      )}
    >
      <span className="block text-xs text-muted-foreground md:sr-only">{label}</span>
      {children}
    </div>
  );
}

function Arrow() {
  return (
    <div className="flex justify-center text-muted-foreground">
      <ArrowDown aria-hidden size={16} className="md:hidden" />
      <ArrowRight aria-hidden size={16} className="hidden md:block" />
    </div>
  );
}

/** The Guide at `/guide` (Start here) and `/guide/$slug`: a page of Markdown under a row of page links. */
export function GuidePage({ slug }: { slug: string | undefined }) {
  const page = findGuidePage(slug);
  return (
    <div className="flex max-w-3xl flex-col gap-4">
      <nav aria-label="Guide pages">
        <ul className="flex flex-wrap gap-2">
          {GUIDE_PAGES.map((p) => (
            <li key={p.slug}>
              <Link
                to={guidePath(p.slug)}
                activeOptions={{ exact: true }}
                className={cn(
                  buttonVariants({ variant: "outline" }),
                  "h-auto py-1 text-xs whitespace-normal aria-[current=page]:bg-accent aria-[current=page]:text-accent-foreground",
                )}
              >
                {p.title}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      {page ? (
        <>
          <PageHeader title={page.title} />
          <Markdown source={page.body} blocks={{ pieces: <PiecesDiagram /> }} />
        </>
      ) : (
        <div role="alert">
          <PageHeader title="Guide page not found" description="There is no Guide page at this address." />
          <Link to={guidePath(START_SLUG)} className={buttonVariants({ variant: "outline" })}>
            Back to Start here
          </Link>
        </div>
      )}
    </div>
  );
}
