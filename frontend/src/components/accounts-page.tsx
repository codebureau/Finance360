import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";

import { AccountSheet } from "@/components/account-form";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fetchAccounts, type Account } from "@/lib/accounts";
import { formatShare } from "@/lib/shares";

export function ownershipText(account: Account): string {
  return account.ownership === "joint"
    ? `Joint: ${account.owners.map((o) => `${o.member_name} ${formatShare(o.share_bp)}`).join(", ")}`
    : `Member: ${account.owners.map((o) => o.member_name).join(", ")}`;
}

/** Invalidate everything an Account change can touch (closing adds a zero Valuation). */
export function useRefreshAccounts() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["accounts"] });
    void queryClient.invalidateQueries({ queryKey: ["tags"] });
    void queryClient.invalidateQueries({ queryKey: ["valuations"] });
    void queryClient.invalidateQueries({ queryKey: ["position"] });
  };
}

function AccountRow({ account }: { account: Account }) {
  const navigate = useNavigate();
  const open = () => void navigate({ to: "/accounts/$accountId", params: { accountId: String(account.id) } });
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
          to="/accounts/$accountId"
          params={{ accountId: String(account.id) }}
          className="font-medium hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          {account.name}
        </Link>
        {account.closed_on && (
          <Badge variant="closed" className="ml-2">
            Closed {account.closed_on}
          </Badge>
        )}
        <p className="text-xs text-muted-foreground md:hidden">
          {account.category_name} · {account.account_type_name}
        </p>
      </TableCell>
      <TableCell className="hidden md:table-cell">{account.category_name}</TableCell>
      <TableCell className="hidden md:table-cell">{account.account_type_name}</TableCell>
      <TableCell className="hidden sm:table-cell">{ownershipText(account)}</TableCell>
      <TableCell className="font-mono">{account.currency}</TableCell>
      <TableCell className="hidden lg:table-cell">
        <span className="flex flex-wrap gap-1">
          {account.tags.map((t) => (
            <span key={t} className="rounded-full bg-muted px-2 py-0.5 text-xs">
              {t}
            </span>
          ))}
        </span>
      </TableCell>
    </TableRow>
  );
}

/** The Accounts list: a compact table whose rows open the Account's detail page. */
export function AccountsPage({
  showClosed = false,
  onShowClosedChange,
}: {
  showClosed?: boolean;
  onShowClosedChange?: (show: boolean) => void;
}) {
  const refresh = useRefreshAccounts();
  const accounts = useQuery({
    queryKey: ["accounts", showClosed],
    queryFn: () => fetchAccounts(showClosed),
  });

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Accounts"
        description="Where the household's money and debts are held."
        action={
          <AccountSheet
            onSaved={refresh}
            trigger={
              <Button>
                <Plus size={16} />
                Add account
              </Button>
            }
          />
        }
      />
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={showClosed}
          onChange={(e) => onShowClosedChange?.(e.target.checked)}
        />
        Show closed
      </label>
      {accounts.isPending && <p className="text-muted-foreground">Loading…</p>}
      {accounts.error && (
        <p className="text-destructive">Could not load the Accounts: {accounts.error.message}</p>
      )}
      {accounts.data?.length === 0 && (
        <p className="text-muted-foreground">
          {showClosed ? "No Accounts yet." : "No open Accounts yet. Add the first one."}
        </p>
      )}
      {accounts.data && accounts.data.length > 0 && (
        <Table aria-label="Accounts">
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead className="hidden md:table-cell">Category</TableHead>
              <TableHead className="hidden md:table-cell">Type</TableHead>
              <TableHead className="hidden sm:table-cell">Ownership</TableHead>
              <TableHead>Currency</TableHead>
              <TableHead className="hidden lg:table-cell">Tags</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {accounts.data.map((a) => (
              <AccountRow key={a.id} account={a} />
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
