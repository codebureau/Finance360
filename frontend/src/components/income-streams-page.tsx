import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";

import { IncomeStreamSheet } from "@/components/income-stream-form";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Money } from "@/components/ui/money";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { endText, fetchIncomeStreams, startText, type IncomeStream } from "@/lib/income-streams";
import { compareScenarios, type Comparison } from "@/lib/scenarios";

/** Invalidate everything an income stream change can touch: the lists and every projection. */
export function useRefreshIncomeStreams() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["income-streams"] });
    void queryClient.invalidateQueries({ queryKey: ["projection"] });
  };
}

/** What a salary projection leaves out, stated beside the streams and on the Projection screen. */
export const INCOME_SIMPLIFICATIONS = [
  "Take-home pay is the amount you enter: tax is not calculated yet. It is credited to Cash each year the stream is in force, grown by the real wage growth Assumption (0% keeps pace with inflation).",
  "Employer super (the Superannuation Guarantee rate, less the flat contributions tax, both from the Rule pack) is added to the owner's Superannuation on top of the salary.",
  "Not modelled: Division 293 tax, concessional contribution caps, the maximum contribution base, salary sacrifice and fund fees.",
  "While anyone is paid a salary from the projection start, spending needs a phase that starts at the projection start (working-years spending).",
  "An annual saving Assumption is not applied to a Member with a salary stream: they save what their salary leaves after spending.",
] as const;

function StreamRow({ stream }: { stream: IncomeStream }) {
  const navigate = useNavigate();
  const open = () => void navigate({ to: "/income-streams/$streamId", params: { streamId: String(stream.id) } });
  const owner = stream.owners.map((o) => o.member_name).join(", ");
  return (
    <TableRow
      className="cursor-pointer"
      onClick={(e) => {
        // The name is a real link (keyboard, middle-click); let it handle its own click.
        if (!(e.target as HTMLElement).closest("a")) open();
      }}
    >
      <TableCell>
        <Link
          to="/income-streams/$streamId"
          params={{ streamId: String(stream.id) }}
          className="font-medium hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          {stream.name}
        </Link>
        <p className="text-xs text-muted-foreground sm:hidden">
          {owner} · {startText(stream)}, {endText(stream).toLowerCase()}
        </p>
      </TableCell>
      <TableCell className="hidden sm:table-cell">{owner}</TableCell>
      <TableCell className="hidden md:table-cell">
        {stream.type_label} <Badge variant="info">{stream.tax_kind}</Badge>
      </TableCell>
      <TableCell className="text-right">
        <Money amount={stream.gross_annual} />
      </TableCell>
      <TableCell className="text-right">
        <Money amount={stream.take_home_annual} />
      </TableCell>
      <TableCell className="hidden lg:table-cell">
        {startText(stream)}, {endText(stream).toLowerCase()}
      </TableCell>
    </TableRow>
  );
}

/** Names the Scenarios a salary has blocked, here where it was added, with the way to fix it. */
function WorkingYearsWarning({ comparison }: { comparison: Comparison }) {
  const blocked = comparison.scenarios
    .filter((s) => s.errors.some((e) => e.field === "spending_phases"))
    .map((s) => s.scenario);
  if (blocked.length === 0) return null;
  return (
    <Card data-testid="working-years-warning">
      <CardContent className="flex flex-col gap-2 text-sm">
        <p>
          {blocked.map((s) => s.name).join(", ")} can&apos;t be projected until spending has a phase that starts at
          the projection start.
        </p>
        <Link
          to="/projection"
          search={{ scenario: blocked[0].id }}
          className="text-primary underline-offset-4 hover:underline"
        >
          Add a spending phase in {blocked[0].name}
        </Link>
      </CardContent>
    </Card>
  );
}

/** The Income streams list: a compact table whose rows open the stream's detail page. */
export function IncomeStreamsPage() {
  const refresh = useRefreshIncomeStreams();
  const streams = useQuery({ queryKey: ["income-streams"], queryFn: fetchIncomeStreams });
  const comparison = useQuery({ queryKey: ["projection", "compare"], queryFn: compareScenarios });
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Income streams"
        description="Pay that funds the household until a Member retires, and the employer super that grows their Superannuation."
        action={
          <IncomeStreamSheet
            onSaved={refresh}
            trigger={
              <Button>
                <Plus size={16} aria-hidden />
                Add income stream
              </Button>
            }
          />
        }
      />
      {streams.isPending && <p className="text-muted-foreground">Loading…</p>}
      {streams.error && <p className="text-destructive">Could not load the income streams: {streams.error.message}</p>}
      {streams.data?.length === 0 && (
        <p className="text-muted-foreground">
          No income streams yet. Add a salary and the Projection credits it until the Member retires.
        </p>
      )}
      {comparison.data && <WorkingYearsWarning comparison={comparison.data} />}
      {streams.data && streams.data.length > 0 && (
        <Table aria-label="Income streams">
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead className="hidden sm:table-cell">Member</TableHead>
              <TableHead className="hidden md:table-cell">Type</TableHead>
              <TableHead className="text-right">Gross a year</TableHead>
              <TableHead className="text-right">Take-home a year</TableHead>
              <TableHead className="hidden lg:table-cell">Years</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {streams.data.map((s) => (
              <StreamRow key={s.id} stream={s} />
            ))}
          </TableBody>
        </Table>
      )}
      <Card data-testid="income-simplifications">
        <CardHeader>
          <CardTitle>What an income stream assumes</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            {INCOME_SIMPLIFICATIONS.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
