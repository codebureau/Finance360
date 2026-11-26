import { useId, useMemo, useState, type ReactNode } from "react";

import { BuildUpChart } from "@/components/build-up-chart";
import { inputClass } from "@/components/form-fields";
import { StatTile } from "@/components/stat-tile";
import { ResultTable, firstName } from "@/components/tax-estimate-view";
import { Money } from "@/components/ui/money";
import { ViewCard } from "@/components/view-card";
import { FALL_TONE, RISE_TONE } from "@/lib/charts/build-up";
import { toneBg } from "@/lib/entity-colour";
import type { TaxHouseholdResult, TaxMemberResult } from "@/lib/tax";
import { householdLines, memberLines, waterfallHeadline, waterfallModel } from "@/lib/tax-waterfall";

const HOUSEHOLD = "household";

/**
 * "How the tax becomes the result" (view `tax.estimate`, shape `build-up`, form `waterfall`): one
 * waterfall at a time, the Household first for a couple, then each Member. The result table is the
 * text equivalent, behind "Show figures". The same card sits in the Tax estimate, a Tax what-if
 * result and a Saved estimate, because all three are built from the same result.
 */
export function TaxWaterfallCard({
  members,
  household,
  incomplete,
  footnote,
}: {
  members: TaxMemberResult[];
  household: TaxHouseholdResult;
  incomplete: boolean;
  /** Beneath the chart, after the net outcome convention (what is not counted, the dependants). */
  footnote?: ReactNode;
}) {
  const couple = members.length > 1;
  const selectId = useId();
  // Page state, not a Display preference: which waterfall is drawn.
  const [pick, setPick] = useState<string>(HOUSEHOLD);
  const member = couple ? members.find((m) => String(m.member_id) === pick) : members[0];
  const who = member ? firstName(member.name) : "Household";

  const lines = useMemo(
    () => (member ? memberLines(member) : householdLines(members, household)),
    [member, members, household],
  );
  const model = useMemo(() => waterfallModel(lines), [lines]);
  const headline = waterfallHeadline(who, lines, incomplete);

  return (
    <ViewCard
      title="How the tax becomes the result"
      headline={headline}
      toolbar={
        couple && (
          <label className="flex items-center gap-2 text-sm" htmlFor={selectId}>
            For
            <select id={selectId} className={inputClass} value={pick} onChange={(e) => setPick(e.target.value)}>
              <option value={HOUSEHOLD}>Household</option>
              {members.map((m) => (
                <option key={m.member_id} value={m.member_id}>
                  {firstName(m.name)}
                </option>
              ))}
            </select>
          </label>
        )
      }
      figures={
        <ResultTable members={members} household={household} />
      }
      figuresName="Estimate"
    >
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start">
        <div className="min-w-0 flex-1">
          <BuildUpChart model={model} what={`how the tax becomes the result for ${who}`} headline={headline} />
        </div>
        <div className="lg:w-60">
          <StatTile
            label="Taxable income"
            hero={<Money amount={lines.taxable_income} />}
            supporting={[
              { label: "Total income", value: <Money amount={lines.total_income} /> },
              { label: "Total deductions", value: <Money amount={lines.total_deductions} /> },
            ]}
            flat
            compact
          />
        </div>
      </div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-label="Key">
        <li className="flex items-center gap-1.5">
          <span aria-hidden className={`size-2.5 rounded-sm ${toneBg(RISE_TONE)}`} />
          Adds to the tax (+)
        </li>
        <li className="flex items-center gap-1.5">
          <span aria-hidden className={`size-2.5 rounded-sm ${toneBg(FALL_TONE)}`} />
          Takes off the tax (-)
        </li>
        <li className="flex items-center gap-1.5">
          <span aria-hidden className={`size-2.5 rounded-sm ${toneBg("ink")}`} />
          A total
        </li>
      </ul>
      <p className="text-sm text-muted-foreground">
        The last bar is total tax less credits, drawn above the line when the tax is more and below it when the credits
        are more. Net outcome in the table is credits less total tax: a negative amount is payable, a positive amount
        is a refund. Division 293 is not in total tax.
        {footnote ? <> {footnote}</> : null}
      </p>
    </ViewCard>
  );
}
