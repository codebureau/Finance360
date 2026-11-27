import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowDown, ArrowRight, ArrowUp } from "lucide-react";

import { GeneralError } from "@/components/form-fields";
import { NotCounted } from "@/components/position-page";
import { StatTile } from "@/components/stat-tile";
import { Money } from "@/components/ui/money";
import { PageHeader } from "@/components/ui/page-header";
import { WhatsDueSection } from "@/components/whats-due-page";
import { netDelta, previousMonthEnd, type DeltaDirection } from "@/lib/home";
import { fetchPosition, fetchPositionHistory } from "@/lib/position";

const ARROW = { up: ArrowUp, down: ArrowDown, flat: ArrowRight } satisfies Record<DeltaDirection, unknown>;

/**
 * Card 1 of Home (view key `home.net-position`, shape `figure`, no severity): net Position in the
 * Base currency, Assets and Liabilities beside it, and the change since the previous month-end in
 * arrow, sign and words. Neutral colour: a rise is not good news or bad news here.
 */
function NetPositionTile() {
  // The same queries (and cache keys) as the Position screen's default view, so the figures agree.
  const position = useQuery({ queryKey: ["position", "", ""], queryFn: () => fetchPosition("", "") });
  const history = useQuery({ queryKey: ["position-history", ""], queryFn: () => fetchPositionHistory("") });
  const p = position.data;
  if (!p) {
    return position.error ? (
      <GeneralError error={position.error} />
    ) : (
      <p className="text-sm text-muted-foreground">Loading...</p>
    );
  }
  const [assets, liabilities] = p.classes;
  const prior = history.data ? previousMonthEnd(history.data.points, p.as_at) : null;
  const delta = netDelta(p.net.household, prior && { on: prior.on, net: prior.net.household }, p.as_at);
  const Arrow = delta ? ARROW[delta.direction] : null;
  return (
    <StatTile
      label="Net position"
      hero={<Money amount={p.net.household} className="font-sans" />}
      headline={delta ? delta.headline : `Net position as at ${p.as_at}.`}
      supporting={[
        { label: "Assets", value: <Money amount={assets.figures.household} className="font-sans" /> },
        { label: "Liabilities", value: <Money amount={liabilities.figures.household} className="font-sans" /> },
      ]}
    >
      {delta && Arrow && (
        <p className="flex items-center gap-1 text-sm text-muted-foreground" data-testid="net-delta">
          <Arrow size={16} aria-hidden />
          {delta.direction !== "flat" && <span className="tabular-nums">{delta.signed}</span>}
          <span>{delta.words}</span>
        </p>
      )}
      <NotCounted p={p} />
      <Link to="/position" className="w-fit text-sm underline">
        Open Position
      </Link>
    </StatTile>
  );
}

/** The home screen (`/`, ADR-0032): net Position above What's due. */
export function HomePage() {
  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <PageHeader title="Home" />
      <NetPositionTile />
      <WhatsDueSection />
    </div>
  );
}
