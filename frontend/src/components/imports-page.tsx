import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { FilePlus } from "lucide-react";
import { useState, type ReactNode } from "react";

import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fetchAccounts } from "@/lib/accounts";
import {
  coverageText,
  detectImport,
  fetchImports,
  importBadge,
  stageImport,
  streamLabel,
  type Detection,
  type ImportSummary,
} from "@/lib/imports";

/** Step two of adding a file: what the file is, and which Account it belongs to. */
function ConfirmFile({
  file,
  detection,
  onCancel,
  onStaged,
}: {
  file: File;
  detection: Detection;
  onCancel: () => void;
  onStaged: (id: number) => void;
}) {
  const accounts = useQuery({ queryKey: ["accounts", "options"], queryFn: () => fetchAccounts(false) });
  const preselected = detection.matches.length === 1 ? String(detection.matches[0].id) : "";
  const [accountId, setAccountId] = useState(preselected);
  const [remember, setRemember] = useState(false);
  const stage = useMutation({
    mutationFn: () => stageImport(file, Number(accountId), remember),
    onSuccess: (result) => onStaged(result.id),
  });
  const matched = detection.matches.some((m) => String(m.id) === accountId);
  const offerRemember = !!detection.reference_masked && !!accountId && !matched;
  const duplicate = detection.duplicate_import_id !== null && matched;

  return (
    <form
      aria-label="Confirm file"
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        stage.mutate();
      }}
    >
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-muted-foreground">File</dt>
        <dd className="break-all">{file.name}</dd>
        <dt className="text-muted-foreground">Format</dt>
        <dd>{detection.importer_name}</dd>
        <dt className="text-muted-foreground">Stream</dt>
        <dd>
          <Badge variant="info">{streamLabel(detection.stream)}</Badge>
        </dd>
        <dt className="text-muted-foreground">Coverage</dt>
        <dd>{coverageText(detection.coverage_from, detection.coverage_to)}</dd>
        <dt className="text-muted-foreground">Rows</dt>
        <dd>{detection.row_count}</dd>
        {detection.reference_masked && (
          <>
            <dt className="text-muted-foreground">Account reference</dt>
            <dd className="font-mono">{detection.reference_masked}</dd>
          </>
        )}
      </dl>
      {detection.warnings.length > 0 && (
        <ul className="list-disc pl-5 text-sm text-muted-foreground">
          {detection.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      <div className="flex flex-col gap-1 text-sm">
        <label htmlFor="file-account">Account</label>
        <select
          id="file-account"
          className={inputClass}
          value={accountId}
          onChange={(e) => setAccountId(e.target.value)}
        >
          <option value="">Choose an Account…</option>
          {accounts.data?.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
        <span className="text-xs text-muted-foreground">
          {detection.matches.length === 1 && "Matched from the file's account reference."}
          {detection.matches.length > 1 && "Several Accounts share this reference, so choose one."}
          {detection.matches.length === 0 && "No Account holds this reference yet, so choose one."}
        </span>
        <FieldMessage error={stage.error} field="account_id" />
      </div>
      {offerRemember && (
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-1"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
          />
          Remember this reference ({detection.reference_masked}) for this Account, so the next file matches
          automatically.
        </label>
      )}
      <FieldMessage error={stage.error} field="value" />
      {duplicate && (
        <p className="text-sm text-muted-foreground">
          This exact file is already imported for this Account.{" "}
          <Link
            to="/imports/$importId"
            params={{ importId: String(detection.duplicate_import_id) }}
            className="underline"
          >
            Open it
          </Link>
          . Staging it again changes nothing.
        </p>
      )}
      <GeneralError error={stage.error} />
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={!accountId || stage.isPending}>
          {duplicate ? "Open existing import" : "Stage file"}
        </Button>
      </DialogFooter>
    </form>
  );
}

/** Add a file in a Dialog: choose a file, read it, confirm the Account, stage it. */
function AddFileDialog({ trigger }: { trigger: ReactNode }) {
  const [open, onOpenChange] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const detect = useMutation({ mutationFn: (f: File) => detectImport(f) });
  const reset = () => {
    setFile(null);
    detect.reset();
  };
  const close = () => {
    onOpenChange(false);
    reset();
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add a file</DialogTitle>
          <DialogDescription>
            A Vanguard Personal Investor trade or cash export. Nothing is imported until you confirm the
            Account.
          </DialogDescription>
        </DialogHeader>
        {!detect.data || !file ? (
          <div className="flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-sm">
              File
              <input
                type="file"
                accept=".csv,text/csv"
                className={inputClass + " py-1.5"}
                onChange={(e) => {
                  const chosen = e.target.files?.[0];
                  if (!chosen) return;
                  setFile(chosen);
                  detect.mutate(chosen);
                }}
              />
              <FieldMessage error={detect.error} field="file" />
            </label>
            <GeneralError error={detect.error} />
            {detect.isPending && <p className="text-sm text-muted-foreground">Reading the file…</p>}
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={close}>
                Cancel
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <ConfirmFile
            file={file}
            detection={detect.data}
            onCancel={close}
            onStaged={(id) => {
              void queryClient.invalidateQueries({ queryKey: ["imports"] });
              close();
              void navigate({ to: "/imports/$importId", params: { importId: String(id) } });
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function ImportRow({ imp }: { imp: ImportSummary }) {
  const navigate = useNavigate();
  const badge = importBadge(imp);
  const open = () => void navigate({ to: "/imports/$importId", params: { importId: String(imp.id) } });
  return (
    <TableRow className="cursor-pointer" onClick={open} aria-label={imp.file_name}>
      <TableCell>
        <Link
          to="/imports/$importId"
          params={{ importId: String(imp.id) }}
          className="font-medium break-all hover:underline"
          onClick={(e) => e.stopPropagation()}
        >
          {imp.file_name}
        </Link>
        <p className="text-xs text-muted-foreground md:hidden">
          {streamLabel(imp.stream)} · {imp.account_name} · {coverageText(imp.coverage_from, imp.coverage_to)}
        </p>
      </TableCell>
      <TableCell className="hidden md:table-cell">
        <Badge variant="info">{streamLabel(imp.stream)}</Badge>
      </TableCell>
      <TableCell className="hidden md:table-cell">{imp.account_name}</TableCell>
      <TableCell className="hidden whitespace-nowrap md:table-cell">
        {coverageText(imp.coverage_from, imp.coverage_to)}
      </TableCell>
      <TableCell className="text-right font-mono tabular-nums">{imp.row_count}</TableCell>
      <TableCell>
        <Badge variant={badge.variant}>{badge.label}</Badge>
      </TableCell>
    </TableRow>
  );
}

/** Imports (one per file): a table whose rows open the detail page. */
export function ImportsPage() {
  const imports = useQuery({ queryKey: ["imports"], queryFn: fetchImports });
  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Imports"
        description="Files from a provider, staged here for review. Each file feeds one Stream of an Account."
        action={
          <AddFileDialog
            trigger={
              <Button>
                <FilePlus size={16} />
                Add file
              </Button>
            }
          />
        }
      />
      {imports.isPending && <p className="text-muted-foreground">Loading…</p>}
      {imports.error && <p className="text-destructive">Could not load the Imports: {imports.error.message}</p>}
      {imports.data?.length === 0 && (
        <p className="text-sm text-muted-foreground">No Imports yet. Add a file to stage it.</p>
      )}
      {imports.data && imports.data.length > 0 && (
        <Table aria-label="Imports">
          <TableHeader>
            <TableRow>
              <TableHead>File</TableHead>
              <TableHead className="hidden md:table-cell">Stream</TableHead>
              <TableHead className="hidden md:table-cell">Account</TableHead>
              <TableHead className="hidden md:table-cell">Coverage</TableHead>
              <TableHead className="text-right">Rows</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {imports.data.map((imp) => (
              <ImportRow key={imp.id} imp={imp} />
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
