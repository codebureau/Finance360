import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, MoreHorizontal } from "lucide-react";
import { useRef, useState } from "react";

import { GeneralError } from "@/components/form-fields";
import { LinkedDocuments } from "@/components/linked-documents";
import { SuperStatementSheet } from "@/components/super-statement-form";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Money } from "@/components/ui/money";
import { PageHeader } from "@/components/ui/page-header";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fetchAccount } from "@/lib/accounts";
import {
  CHECK_LABELS,
  fetchSuperStatement,
  fetchSuperStatements,
  OUTFLOW_FIELDS,
  RECEIVED_FIELDS,
  removeSuperStatement,
  ROLL_FIELDS,
  SUPER_STATEMENT_KIND,
  type SuperStatement,
} from "@/lib/super-statements";
import { financialYearLabel } from "@/lib/tax";

type Account = { id: number; name: string; currency: string; closed_on?: string | null };

function BackLink({ accountId }: { accountId: number }) {
  return (
    <Link
      to="/accounts/$accountId"
      params={{ accountId: String(accountId) }}
      className={buttonVariants({ variant: "ghost" }) + " -ml-3 w-fit"}
    >
      <ArrowLeft size={16} aria-hidden />
      Account
    </Link>
  );
}

/** A figure the statement states, or "Not stated" (which is not zero). */
function Figure({ label, amount, currency }: { label: string; amount: string | null; currency: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-sm">
        {amount === null ? (
          <span className="text-muted-foreground">Not stated</span>
        ) : (
          <Money amount={amount} currency={currency} />
        )}
      </dd>
    </div>
  );
}

function FigureCard({
  title,
  fields,
  statement,
}: {
  title: string;
  fields: readonly { key: keyof SuperStatement & string; label: string }[];
  statement: SuperStatement;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="flex flex-col divide-y">
          {fields.map((f) => (
            <Figure
              key={f.key}
              label={f.label}
              amount={statement[f.key] as string | null}
              currency={statement.currency}
            />
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}

function ChecksCard({ statement }: { statement: SuperStatement }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Checks</CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="flex flex-col divide-y" aria-label="Checks">
          {statement.checks.map((c) => {
            const ticked = statement.ticked_checks.includes(c.name);
            return (
              <li key={c.name} className="flex flex-col gap-1 py-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{CHECK_LABELS[c.name] ?? c.name}</span>
                  {c.status === "ok" && <Badge variant="ok">Agrees</Badge>}
                  {c.status === "not_applicable" && <Badge variant="closed">Not applicable</Badge>}
                  {c.status === "warn" && !ticked && <Badge variant="flagged">Does not agree</Badge>}
                  {ticked && <Badge variant="flagged">You checked this</Badge>}
                </div>
                {c.status !== "ok" && <p className="text-muted-foreground">{c.message}</p>}
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}

function StatementView({ account, statement }: { account: Account; statement: SuperStatement }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [correcting, setCorrecting] = useState(false);
  const [removing, setRemoving] = useState(false);
  const menuRef = useRef<HTMLButtonElement>(null);
  const siblings = useQuery({
    queryKey: ["super-statements", account.id],
    queryFn: () => fetchSuperStatements(account.id),
  });
  const takenYears = (siblings.data ?? []).map((s) => s.financial_year);
  const year = financialYearLabel(statement.financial_year);

  const refresh = () => {
    for (const key of [
      ["super-statements"],
      ["super-statement"],
      ["valuations", account.id],
      ["position"],
    ]) {
      void queryClient.invalidateQueries({ queryKey: key });
    }
  };
  const remove = useMutation({
    mutationFn: () => removeSuperStatement(statement.id),
    onSuccess: () => {
      refresh();
      void navigate({ to: "/accounts/$accountId", params: { accountId: String(account.id) } });
    },
  });

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <BackLink accountId={account.id} />
        <PageHeader
          title={`Super statement FY ${year}`}
          description={
            <>
              {account.name} · {statement.period_start} to {statement.period_end} · {statement.currency}
            </>
          }
          action={
            <div className="flex items-center gap-2">
              <Button variant="outline" onClick={() => setCorrecting(true)}>
                Correct Super statement
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button ref={menuRef} variant="outline" aria-label="More actions" className="px-2">
                    <MoreHorizontal size={16} aria-hidden />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent>
                  <DropdownMenuItem destructive onSelect={() => setRemoving(true)}>
                    Remove Super statement
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          }
        />
      </div>

      <FigureCard title="Balance roll" fields={ROLL_FIELDS} statement={statement} />
      <FigureCard title="Outflows" fields={OUTFLOW_FIELDS} statement={statement} />
      <FigureCard title="Contributions received" fields={RECEIVED_FIELDS} statement={statement} />

      <Card aria-label="Investment options" role="region">
        <CardHeader>
          <CardTitle>Investment options</CardTitle>
        </CardHeader>
        <CardContent>
          {statement.options.length === 0 ? (
            <p className="text-sm text-muted-foreground">The statement lists no Investment options.</p>
          ) : (
            <Table aria-label="Investment options">
              <TableHeader>
                <TableRow>
                  <TableHead>Option</TableHead>
                  <TableHead className="hidden text-right sm:table-cell">Units</TableHead>
                  <TableHead className="hidden text-right sm:table-cell">Unit price</TableHead>
                  <TableHead className="text-right">Value ({statement.currency})</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {statement.options.map((o, i) => (
                  <TableRow key={`${o.name}-${i}`}>
                    <TableCell className="break-words">
                      {o.name}
                      {(o.units !== null || o.unit_price !== null) && (
                        <span className="block text-xs text-muted-foreground sm:hidden">
                          {o.units ?? "—"} units at {o.unit_price ?? "—"}
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="hidden text-right font-mono tabular-nums sm:table-cell">
                      {o.units ?? "—"}
                    </TableCell>
                    <TableCell className="hidden text-right font-mono tabular-nums sm:table-cell">
                      {o.unit_price ?? "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      <Money amount={o.value} currency={statement.currency} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <ChecksCard statement={statement} />

      <LinkedDocuments kind={SUPER_STATEMENT_KIND} id={statement.id} />

      <SuperStatementSheet
        account={account}
        statement={statement}
        open={correcting}
        onOpenChange={setCorrecting}
        takenYears={takenYears}
        onSaved={refresh}
      />
      <AlertDialog open={removing} onOpenChange={setRemoving}>
        <AlertDialogContent
          onCloseAutoFocus={(e) => {
            e.preventDefault();
            menuRef.current?.focus();
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>
              Remove the {year} Super statement of {account.name}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              Its 30 June Valuation is removed with it. Both stay in the history, marked as removed, and its
              documents stay in the Library.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <GeneralError error={remove.error} />
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={remove.isPending}
              onClick={(e) => {
                e.preventDefault(); // stay open until the server accepts, so errors show here
                remove.mutate();
              }}
            >
              Remove Super statement
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/**
 * `/accounts/$accountId/super-statements/$statementId`: a Super statement read-only (ADR-0026
 * slice 4): the balance roll, outflows, contributions received, Investment options and the ticked
 * checks, with Correct and Remove, and the statement's documents. An address saved before a
 * correction still opens the statement as it stands.
 */
export function SuperStatementDetailPage({
  accountId,
  statementId,
}: {
  accountId: string;
  statementId: string;
}) {
  const aid = Number(accountId);
  const sid = Number(statementId);
  const valid = Number.isInteger(aid) && aid > 0 && Number.isInteger(sid) && sid > 0;
  const statement = useQuery({
    queryKey: ["super-statement", sid],
    queryFn: () => fetchSuperStatement(sid),
    enabled: valid,
    retry: false,
  });
  const account = useQuery({
    queryKey: ["accounts", "detail", aid],
    queryFn: () => fetchAccount(aid),
    enabled: valid,
    retry: false,
  });

  if (statement.data && account.data && statement.data.account_id === aid)
    return <StatementView account={account.data} statement={statement.data} />;
  if (!valid || statement.error || account.error || (statement.data && statement.data.account_id !== aid)) {
    return (
      <div className="flex flex-col gap-3">
        <PageHeader
          title="Super statement not found"
          description="There is no current Super statement at this address. It may have been removed."
        />
        <div>
          {valid ? (
            <BackLink accountId={aid} />
          ) : (
            <Link to="/accounts" className={buttonVariants({ variant: "ghost" }) + " -ml-3 w-fit"}>
              <ArrowLeft size={16} aria-hidden />
              Accounts
            </Link>
          )}
        </div>
      </div>
    );
  }
  return <p className="text-muted-foreground">Loading…</p>;
}
