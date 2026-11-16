import { Link } from "@tanstack/react-router";

import { FigureSource, StepLine } from "@/components/tax-record-trace";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Money } from "@/components/ui/money";
import { financialYearLabel, type TaxDiv293, type TaxDiv293Member } from "@/lib/tax";

const SOURCE_NAME: Record<string, string> = {
  statement: "Super statement",
  income_statement: "Income statements",
  income_stream: "Income stream",
  none: "no source",
};

/** What the fund released for Division 293 beside what the measure predicts, where a statement states it. */
function Actual({ member }: { member: TaxDiv293Member }) {
  if (member.released === null) return null;
  return (
    <p data-testid="div293-actual" className="text-sm">
      Released from super for Division 293 in this year, from the {member.released_source ?? "Super statement"}:{" "}
      <Money amount={member.released} />. Predicted: <Money amount={member.liability} />. The ATO assesses Division 293
      after the return, so a release in one year is usually for an earlier year.
    </p>
  );
}

function CapCheck({ member }: { member: TaxDiv293Member }) {
  return (
    <div data-testid="div293-cap" className="text-sm">
      <p>
        Concessional contributions received <Money amount={member.received} /> against the cap{" "}
        <Money amount={member.cap} /> ({SOURCE_NAME[member.contributions_source] ?? member.contributions_source}).
      </p>
      {member.cap_warning && (
        <p role="status" className="mt-1 rounded-md border border-warning/40 bg-warning/10 p-2">
          <Badge variant="flagged">{member.cap_status === "over" ? "Over the cap" : "Near the cap"}</Badge>{" "}
          {member.cap_warning}
        </p>
      )}
    </div>
  );
}

function MemberWorking({ member }: { member: TaxDiv293Member }) {
  return (
    <section aria-label={`Division 293 for ${member.name}`} className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-base font-semibold">{member.name}</h3>
        <span className="text-sm">
          Division 293 tax <Money amount={member.liability} className="font-semibold" />
        </span>
        {!member.applies && <span className="text-sm text-muted-foreground">None due.</span>}
      </div>
      <Actual member={member} />
      <CapCheck member={member} />
      <div className="flex flex-col gap-2">
        <h4 className="text-sm font-semibold">What goes in</h4>
        <ul className="flex flex-col gap-2">
          {member.inputs.map((input, i) => (
            <li key={i} className="flex flex-col text-sm">
              <span className="flex flex-wrap items-baseline justify-between gap-x-3">
                <span className="font-medium">{input.label}</span>
                <Money amount={input.amount} />
              </span>
              <span className="text-xs text-muted-foreground">{input.source}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="flex flex-col gap-2">
        <h4 className="text-sm font-semibold">How it is worked out</h4>
        <ol className="flex flex-col gap-4">
          {member.steps.map((step, i) => (
            <StepLine key={i} step={step} />
          ))}
        </ol>
      </div>
      {member.notes.length > 0 && (
        <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
          {member.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * Division 293 for the year, per Member: the tax, each input with its value and source, then the
 * steps to the result including the "lesser of" comparison. The Rule pack figures it read are listed
 * with where each came from. Computed by the server now; nothing is saved.
 */
export function Division293({ data, year }: { data: TaxDiv293; year: number }) {
  if (data.members.length === 0) {
    return (
      <Card aria-labelledby="div293-title" data-testid="div293-waiting">
        <CardHeader>
          <CardTitle id="div293-title">Division 293</CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          {data.waiting_for.length > 0 ? (
            <>
              Division 293 is waiting for {data.waiting_for.map((w) => w.name).join(", ")} (FY{" "}
              {financialYearLabel(year)}). Nothing is guessed, so it is not worked out until{" "}
              {data.waiting_for.length === 1 ? "it is" : "they are"} captured.{" "}
              <Link to="/rule-figures" search={{ fy: year }} className="text-primary underline underline-offset-4">
                Capture on Rule figures (FY {financialYearLabel(year)})
              </Link>
              .
            </>
          ) : (
            "Division 293 could not be worked out."
          )}
        </CardContent>
      </Card>
    );
  }
  const provisional = data.figures_used.some((f) => f.provisional);
  return (
    <Card aria-labelledby="div293-title" data-testid="div293">
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle id="div293-title">Division 293 for FY {financialYearLabel(year)}</CardTitle>
          {provisional && <Badge variant="flagged">Provisional</Badge>}
        </div>
        <p className="text-sm text-muted-foreground">
          An extra tax on super contributions for high earners, assessed by the ATO separately, after the return.
          This is the full measure; the notice itself arrives later.{" "}
          <Link to="/guide/division-293" className="text-primary underline underline-offset-4">
            How it works
          </Link>
          .
        </p>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        {data.members.map((member) => (
          <MemberWorking key={member.member_id} member={member} />
        ))}
        {data.figures_used.length > 0 && (
          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold">Where the Rule pack figures come from</h3>
            <ul className="flex flex-col gap-3">
              {data.figures_used.map((figure) => (
                <FigureSource key={`${figure.key}-${figure.financial_year}`} figure={figure} />
              ))}
            </ul>
          </section>
        )}
      </CardContent>
    </Card>
  );
}
