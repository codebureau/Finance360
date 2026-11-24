import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowLeft, MoreHorizontal } from "lucide-react";
import { useRef, useState } from "react";

import { AccountIdentifiersSection } from "@/components/account-identifiers-section";
import { AccountSheet } from "@/components/account-form";
import { ownershipText, useRefreshAccounts } from "@/components/accounts-page";
import {
  FieldMessage,
  GeneralError,
  inputClass,
} from "@/components/form-fields";
import { DerivedValuePanel } from "@/components/derived-value-panel";
import { HoldingsPanel } from "@/components/holdings-panel";
import { LinkedDocuments } from "@/components/linked-documents";
import { LookThroughPanel } from "@/components/look-through-panel";
import { SuperStatementsPanel } from "@/components/super-statements-panel";
import { TransactionsPanel } from "@/components/transactions-panel";
import { ValuationsPanel } from "@/components/valuations-panel";
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
import { PageHeader } from "@/components/ui/page-header";
import { closeAccount, fetchAccount, type Account } from "@/lib/accounts";
import { fetchCatalogue } from "@/lib/catalogue";
import { ACCOUNT_KIND, SUPER_ACCOUNT_TYPE } from "@/lib/super-statements";
import { ApiError, fetchHousehold, todayIn } from "@/lib/household";
import type { TransactionsSearch } from "@/lib/transactions";

/** Used until the Household loads; the Household time zone replaces it. */
const DEFAULT_TIME_ZONE = "Australia/Melbourne";

function BackLink() {
  return (
    <Link
      to="/accounts"
      className={buttonVariants({ variant: "ghost" }) + " -ml-3 w-fit"}
    >
      <ArrowLeft size={16} />
      Accounts
    </Link>
  );
}

/**
 * Close account, always confirmed. A non-zero latest Valuation answers 409; the same dialog then
 * asks "Close anyway?" before sending the explicit confirmation (ADR-0009).
 */
function CloseAccountDialog({
  account,
  timeZone,
  open,
  onOpenChange,
  onClosed,
  onCloseAutoFocus,
}: {
  account: Account;
  timeZone: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onClosed: () => void;
  onCloseAutoFocus: (e: Event) => void;
}) {
  const [closedOn, setClosedOn] = useState(() => todayIn(timeZone));
  const close = useMutation({
    mutationFn: (confirmNonZero: boolean) =>
      closeAccount(account.id, closedOn, confirmNonZero),
    onSuccess: () => {
      onOpenChange(false);
      onClosed();
    },
  });
  const needsConfirmation =
    close.error instanceof ApiError && close.error.status === 409;

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close.reset();
        onOpenChange(next);
      }}
    >
      <AlertDialogContent onCloseAutoFocus={onCloseAutoFocus}>
        <AlertDialogHeader>
          <AlertDialogTitle>Close {account.name}?</AlertDialogTitle>
          <AlertDialogDescription>
            Closing keeps the history. The Account stops counting from the date
            below.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <label className="flex flex-col gap-1 text-sm">
          Closed on
          <input
            className={inputClass}
            type="date"
            value={closedOn}
            onChange={(e) => setClosedOn(e.target.value)}
          />
        </label>
        <FieldMessage error={close.error} field="closed_on" />
        <GeneralError error={close.error} />
        {needsConfirmation && (
          <p className="text-sm font-medium">Close anyway?</p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            disabled={close.isPending}
            onClick={(e) => {
              e.preventDefault(); // stay open until the server accepts, so errors show here
              close.mutate(needsConfirmation);
            }}
          >
            {needsConfirmation ? "Close anyway" : "Close account"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function Detail({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

function AccountDetail({
  account,
  transactions,
  onTransactions,
}: {
  account: Account;
  transactions: TransactionsSearch;
  onTransactions: (patch: Partial<TransactionsSearch>) => void;
}) {
  const refresh = useRefreshAccounts();
  const [closing, setClosing] = useState(false);
  const menuRef = useRef<HTMLButtonElement>(null);
  const household = useQuery({
    queryKey: ["household"],
    queryFn: fetchHousehold,
  });
  const catalogue = useQuery({
    queryKey: ["catalogue"],
    queryFn: fetchCatalogue,
  });
  const timeZone = household.data?.time_zone ?? DEFAULT_TIME_ZONE;
  const liability = !!catalogue.data?.classes.some(
    (c) =>
      c.class === "liability" &&
      c.categories.some((cat) => cat.key === account.category_key),
  );

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <BackLink />
        <PageHeader
          title={account.name}
          description={
            <>
              {account.category_name} · {account.account_type_name} ·{" "}
              {account.currency}
              {account.closed_on && (
                <Badge variant="closed" className="ml-2">
                  Closed {account.closed_on}
                </Badge>
              )}
            </>
          }
          action={
            <div className="flex items-center gap-2">
              <AccountSheet
                account={account}
                onSaved={refresh}
                trigger={<Button variant="outline">Edit account</Button>}
              />
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    ref={menuRef}
                    variant="outline"
                    aria-label="More actions"
                    className="px-2"
                  >
                    <MoreHorizontal size={16} />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent>
                  {account.closed_on ? (
                    <DropdownMenuItem disabled>Already closed</DropdownMenuItem>
                  ) : (
                    <DropdownMenuItem
                      destructive
                      onSelect={() => setClosing(true)}
                    >
                      Close account
                    </DropdownMenuItem>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          }
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Details</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-4 sm:grid-cols-2">
            <Detail label="Ownership">{ownershipText(account)}</Detail>
            <Detail label="Opened on">
              {account.opened_on ?? "Not recorded"}
            </Detail>
            <Detail label="Tags">
              {account.tags.length === 0 ? (
                "None"
              ) : (
                <span className="flex flex-wrap gap-1">
                  {account.tags.map((t) => (
                    <span
                      key={t}
                      className="rounded-full bg-muted px-2 py-0.5 text-xs"
                    >
                      {t}
                    </span>
                  ))}
                </span>
              )}
            </Detail>
            {account.notes && <Detail label="Notes">{account.notes}</Detail>}
          </dl>
        </CardContent>
      </Card>

      <ValuationsPanel
        account={account}
        timeZone={timeZone}
        liability={liability}
      />

      {account.account_type_key === SUPER_ACCOUNT_TYPE && (
        <>
          <SuperStatementsPanel account={account} />
          <LookThroughPanel
            account={account}
            asAt={transactions.asat}
            dimension={transactions.dimension}
            onDimension={(dimension) => onTransactions({ dimension })}
          />
        </>
      )}

      {account.category_key === "investments" && (
        <>
          <DerivedValuePanel
            account={account}
            asAt={transactions.asat}
            onAsAt={(asat) => onTransactions({ asat })}
          />
          <LookThroughPanel
            account={account}
            asAt={transactions.asat}
            dimension={transactions.dimension}
            onDimension={(dimension) => onTransactions({ dimension })}
          />
          <HoldingsPanel
            account={account}
            asAt={transactions.asat}
            onAsAt={(asat) => onTransactions({ asat })}
          />
        </>
      )}

      <TransactionsPanel
        account={account}
        search={transactions}
        onSearch={onTransactions}
      />

      <AccountIdentifiersSection account={account} />

      <LinkedDocuments kind={ACCOUNT_KIND} id={account.id} />

      <div>
        <Link
          to="/audit"
          search={{ account_id: account.id }}
          // Long Account names must wrap, not push the page wider than a phone (390px).
          className={`${buttonVariants({ variant: "link" })} h-auto max-w-full whitespace-normal break-words px-0 text-left`}
        >
          View audit trail for {account.name}
        </Link>
      </div>

      {!account.closed_on && (
        <CloseAccountDialog
          key={account.id}
          account={account}
          timeZone={timeZone}
          open={closing}
          onOpenChange={setClosing}
          onClosed={refresh}
          onCloseAutoFocus={(e) => {
            // Opened from a menu item, so hand focus back to the menu button.
            e.preventDefault();
            menuRef.current?.focus();
          }}
        />
      )}
    </div>
  );
}

/** `/accounts/$accountId`: the Account's details, Valuations, identifiers and audit link. */
export function AccountDetailPage({
  accountId,
  transactions = {},
  onTransactions = () => undefined,
}: {
  accountId: string;
  transactions?: TransactionsSearch;
  onTransactions?: (patch: Partial<TransactionsSearch>) => void;
}) {
  const id = Number(accountId);
  const valid = Number.isInteger(id) && id > 0;
  const account = useQuery({
    queryKey: ["accounts", "detail", id],
    queryFn: () => fetchAccount(id),
    enabled: valid,
    retry: false,
  });

  if (account.data)
    return (
      <AccountDetail
        account={account.data}
        transactions={transactions}
        onTransactions={onTransactions}
      />
    );
  if (!valid || account.error) {
    return (
      <div className="flex flex-col gap-3">
        <PageHeader
          title="Account not found"
          description="There is no Account at this address."
        />
        <div>
          <BackLink />
        </div>
      </div>
    );
  }
  return <p className="text-muted-foreground">Loading…</p>;
}
