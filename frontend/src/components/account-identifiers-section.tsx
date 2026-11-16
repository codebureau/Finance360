import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff, MoreHorizontal, Plus } from "lucide-react";
import { useRef, useState } from "react";

import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
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
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  addAccountIdentifier,
  fetchAccountIdentifiers,
  identifierKindLabel,
  KINDS,
  removeAccountIdentifier,
  revealAccountIdentifier,
  type AccountIdentifier,
} from "@/lib/account-identifiers";
import type { Account } from "@/lib/accounts";

function AddIdentifierForm({
  account,
  onDone,
  onCancel,
}: {
  account: Account;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [kind, setKind] = useState<string>("vendor_account_id");
  const [value, setValue] = useState("");
  const [institution, setInstitution] = useState("");
  const add = useMutation({
    mutationFn: () =>
      addAccountIdentifier(account.id, { kind, value, institution: institution.trim() ? institution : null }),
    onSuccess: onDone,
  });
  return (
    <form
      aria-label={`Add identifier to ${account.name}`}
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        add.mutate();
      }}
    >
      <label className="flex flex-col gap-1 text-sm">
        Kind
        <select className={inputClass} value={kind} onChange={(e) => setKind(e.target.value)}>
          {KINDS.map((k) => (
            <option key={k.key} value={k.key}>
              {k.label}
            </option>
          ))}
        </select>
        <FieldMessage error={add.error} field="kind" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Identifier
        <input
          className={inputClass}
          value={value}
          autoComplete="off"
          onChange={(e) => setValue(e.target.value)}
        />
        <FieldMessage error={add.error} field="value" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Institution (optional)
        <input className={inputClass} value={institution} onChange={(e) => setInstitution(e.target.value)} />
        <FieldMessage error={add.error} field="institution" />
      </label>
      <GeneralError error={add.error} />
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={add.isPending}>
          Add identifier
        </Button>
      </DialogFooter>
    </form>
  );
}

function IdentifierRow({ account, identifier, onChange }: { account: Account; identifier: AccountIdentifier; onChange: () => void }) {
  const [revealed, setRevealed] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const menuRef = useRef<HTMLButtonElement>(null);
  const reveal = useMutation({
    mutationFn: () => revealAccountIdentifier(account.id, identifier.id),
    onSuccess: setRevealed,
  });
  const remove = useMutation({
    mutationFn: () => removeAccountIdentifier(account.id, identifier.id),
    onSuccess: () => {
      setRemoving(false);
      onChange();
    },
  });
  const label = `${identifierKindLabel(identifier.kind)} ${identifier.masked_value}`;
  return (
    <TableRow>
      <TableCell>
        {identifierKindLabel(identifier.kind)}
        {identifier.institution && (
          <p className="text-xs text-muted-foreground sm:hidden">{identifier.institution}</p>
        )}
      </TableCell>
      <TableCell className="hidden sm:table-cell">{identifier.institution ?? "—"}</TableCell>
      <TableCell>
        <span className="flex items-center gap-1">
          <span className={revealed ? "font-mono break-all" : "font-mono whitespace-nowrap"} data-testid="identifier-value">
            {revealed ?? identifier.masked_value}
          </span>
          {revealed === null ? (
            <Button
              variant="ghost"
              className="px-2"
              aria-label={`Reveal ${label}`}
              disabled={reveal.isPending}
              onClick={() => reveal.mutate()}
            >
              <Eye size={16} />
            </Button>
          ) : (
            <Button variant="ghost" className="px-2" aria-label={`Hide ${label}`} onClick={() => setRevealed(null)}>
              <EyeOff size={16} />
            </Button>
          )}
        </span>
        <GeneralError error={reveal.error} />
      </TableCell>
      <TableCell className="w-10 text-right">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button ref={menuRef} variant="ghost" aria-label={`Actions for ${label}`} className="px-2">
              <MoreHorizontal size={16} />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuItem destructive onSelect={() => setRemoving(true)}>
              Remove identifier
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <AlertDialog open={removing} onOpenChange={setRemoving}>
          <AlertDialogContent
            onCloseAutoFocus={(e) => {
              e.preventDefault();
              menuRef.current?.focus();
            }}
          >
            <AlertDialogHeader>
              <AlertDialogTitle>Remove the {label}?</AlertDialogTitle>
              <AlertDialogDescription>
                Files carrying this reference will no longer match {account.name} automatically. Past imports are
                not affected.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <GeneralError error={remove.error} />
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction
                disabled={remove.isPending}
                onClick={(e) => {
                  e.preventDefault();
                  remove.mutate();
                }}
              >
                Remove identifier
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </TableCell>
    </TableRow>
  );
}

/**
 * The Account's identifiers (BSB and account number, member number, a provider's account id…). They
 * are sensitive, so the list shows them masked; the eye button makes the explicit reveal call.
 */
export function AccountIdentifiersSection({ account }: { account: Account }) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const identifiers = useQuery({
    queryKey: ["account-identifiers", account.id],
    queryFn: () => fetchAccountIdentifiers(account.id),
  });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["account-identifiers", account.id] });
  return (
    <Card aria-label={`Identifiers of ${account.name}`}>
      <Dialog open={adding} onOpenChange={setAdding}>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
          <div className="flex flex-col gap-1">
            <CardTitle>Identifiers</CardTitle>
            <p className="text-sm text-muted-foreground">
              Match imported files to this Account. Shown masked; reveal one to read it.
            </p>
          </div>
          <DialogTrigger asChild>
            <Button variant="outline">
              <Plus size={16} />
              Add identifier
            </Button>
          </DialogTrigger>
        </CardHeader>
        <CardContent>
          {identifiers.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
          {identifiers.error && (
            <p className="text-sm text-destructive">Could not load the identifiers: {identifiers.error.message}</p>
          )}
          {identifiers.data?.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No identifiers. Add one so files can find this Account on their own.
            </p>
          )}
          {identifiers.data && identifiers.data.length > 0 && (
            <Table aria-label="Identifiers">
              <TableHeader>
                <TableRow>
                  <TableHead>Kind</TableHead>
                  <TableHead className="hidden sm:table-cell">Institution</TableHead>
                  <TableHead>Identifier</TableHead>
                  <TableHead>
                    <span className="sr-only">Actions</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {identifiers.data.map((i) => (
                  <IdentifierRow key={i.id} account={account} identifier={i} onChange={refresh} />
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add identifier</DialogTitle>
            <DialogDescription>
              It is stored only in this instance and shown masked. Each identifier names one Account.
            </DialogDescription>
          </DialogHeader>
          <AddIdentifierForm
            account={account}
            onCancel={() => setAdding(false)}
            onDone={() => {
              setAdding(false);
              refresh();
            }}
          />
        </DialogContent>
      </Dialog>
    </Card>
  );
}
