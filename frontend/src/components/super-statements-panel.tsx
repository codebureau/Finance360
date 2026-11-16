import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { MoreHorizontal, Plus } from "lucide-react";
import { useRef, useState } from "react";

import { GeneralError } from "@/components/form-fields";
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
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Money } from "@/components/ui/money";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  CHECK_LABELS,
  fetchSuperStatements,
  removeSuperStatement,
  type SuperStatement,
} from "@/lib/super-statements";
import { financialYearLabel } from "@/lib/tax";

type Account = { id: number; name: string; currency: string; closed_on?: string | null };

function StatementRow({
  account,
  statement,
  takenYears,
  onChange,
}: {
  account: Account;
  statement: SuperStatement;
  takenYears: readonly number[];
  onChange: () => void;
}) {
  const [correcting, setCorrecting] = useState(false);
  const [removing, setRemoving] = useState(false);
  const menuRef = useRef<HTMLButtonElement>(null);
  const navigate = useNavigate();
  const open = () =>
    void navigate({
      to: "/accounts/$accountId/super-statements/$statementId",
      params: { accountId: String(account.id), statementId: String(statement.id) },
    });
  const remove = useMutation({
    mutationFn: () => removeSuperStatement(statement.id),
    onSuccess: onChange,
  });
  const year = financialYearLabel(statement.financial_year);
  const name = `Super statement ${year}`;
  const returnFocus = (e: Event) => {
    e.preventDefault();
    menuRef.current?.focus();
  };

  return (
    <TableRow
      className="cursor-pointer"
      onClick={(e) => {
        // The row opens the statement; its own links, buttons and dialogs keep their clicks.
        if (!(e.target as HTMLElement).closest("a, button, [role=menuitem], [role=dialog]")) open();
      }}
    >
      <TableCell>
        <Link
          to="/accounts/$accountId/super-statements/$statementId"
          params={{ accountId: String(account.id), statementId: String(statement.id) }}
          className="font-medium whitespace-nowrap text-primary underline underline-offset-4"
        >
          FY {year}
        </Link>
        <p className="text-xs font-normal text-muted-foreground">
          {statement.options.length > 0
            ? `${statement.options.length} Investment option${statement.options.length === 1 ? "" : "s"}`
            : "Balance roll only"}
        </p>
        {statement.ticked_checks.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {statement.ticked_checks.map((c) => (
              <Badge key={c} variant="flagged" className="whitespace-normal">
                You checked: {CHECK_LABELS[c] ?? c}
              </Badge>
            ))}
          </div>
        )}
        <GeneralError error={remove.error} />
      </TableCell>
      <TableCell className="text-right whitespace-nowrap">
        <Money amount={statement.closing} currency={statement.currency} />
      </TableCell>
      <TableCell className="w-10 text-right">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              ref={menuRef}
              variant="ghost"
              aria-label={`Actions for ${name}`}
              className="px-2"
            >
              <MoreHorizontal size={16} />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuItem onSelect={() => setCorrecting(true)}>
              Correct Super statement
            </DropdownMenuItem>
            <DropdownMenuItem destructive onSelect={() => setRemoving(true)}>
              Remove Super statement
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <SuperStatementSheet
          account={account}
          statement={statement}
          open={correcting}
          onOpenChange={setCorrecting}
          takenYears={takenYears}
          onSaved={onChange}
        />
        <AlertDialog open={removing} onOpenChange={setRemoving}>
          <AlertDialogContent onCloseAutoFocus={returnFocus}>
            <AlertDialogHeader>
              <AlertDialogTitle>
                Remove the {year} Super statement of {account.name}?
              </AlertDialogTitle>
              <AlertDialogDescription>
                Its 30 June Valuation is removed with it. Both stay in the history, marked as
                removed.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction onClick={() => remove.mutate()}>
                Remove Super statement
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </TableCell>
    </TableRow>
  );
}

/**
 * An Account's Super statements by financial year (ADR-0025): add and correct in a Sheet, remove
 * after confirming. Each one records the 30 June Valuation, so the Valuations list changes too.
 */
export function SuperStatementsPanel({ account }: { account: Account }) {
  const queryClient = useQueryClient();
  const key = ["super-statements", account.id];
  const { data, error, isPending } = useQuery({
    queryKey: key,
    queryFn: () => fetchSuperStatements(account.id),
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: key });
    void queryClient.invalidateQueries({ queryKey: ["valuations", account.id] });
    void queryClient.invalidateQueries({ queryKey: ["position"] });
  };
  const takenYears = (data ?? []).map((s) => s.financial_year);

  return (
    <Card aria-label={`Super statements of ${account.name}`} role="region">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle>Super statements</CardTitle>
        <SuperStatementSheet
          account={account}
          takenYears={takenYears}
          onSaved={refresh}
          trigger={
            <Button variant="outline">
              <Plus size={16} />
              Add Super statement
            </Button>
          }
        />
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {isPending && <p className="text-muted-foreground">Loading…</p>}
        {error && (
          <p className="text-destructive">Could not load the Super statements: {error.message}</p>
        )}
        {data?.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No Super statements yet. Type in the fund&apos;s annual statement once it is issued
            (usually August to September); it records the 30 June Valuation too.
          </p>
        )}
        {data && data.length > 0 && (
          <Table aria-label="Super statements">
            <TableHeader>
              <TableRow>
                <TableHead>Financial year</TableHead>
                <TableHead className="text-right">Closing ({account.currency})</TableHead>
                <TableHead>
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((s) => (
                <StatementRow
                  key={s.id}
                  account={account}
                  statement={s}
                  takenYears={takenYears}
                  onChange={refresh}
                />
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
