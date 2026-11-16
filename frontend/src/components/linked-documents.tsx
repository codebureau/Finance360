import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { MoreHorizontal, Paperclip } from "lucide-react";
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  ACCEPT,
  addAndLink,
  checkLibrary,
  fetchLibraryDocuments,
  fetchLinkedDocuments,
  fileSizeText,
  libraryFileUrl,
  linkLibraryDocument,
  mediaTypeName,
  PROBLEM_TEXT,
  unlinkLibraryDocument,
  type LibraryLinkedDocument,
} from "@/lib/library";

/** A record's documents, in the order linked. */
export function useLinkedDocuments(kind: string, id: number) {
  return useQuery({ queryKey: ["library-links", kind, id], queryFn: () => fetchLinkedDocuments(kind, id) });
}

/** Everything a change to a document or a link makes stale. */
export function useRefreshLibrary() {
  const queryClient = useQueryClient();
  return () => {
    for (const key of ["library-links", "library-documents", "library-document", "library-check"]) {
      void queryClient.invalidateQueries({ queryKey: [key] });
    }
  };
}

function UnlinkDialog({
  item,
  onClose,
}: {
  item: LibraryLinkedDocument | null;
  onClose: () => void;
}) {
  const refresh = useRefreshLibrary();
  const unlink = useMutation({
    mutationFn: (linkId: number) => unlinkLibraryDocument(linkId),
    onSuccess: () => {
      refresh();
      onClose();
    },
  });
  return (
    <AlertDialog
      open={item !== null}
      onOpenChange={(next) => {
        if (!next) {
          unlink.reset();
          onClose();
        }
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Unlink {item?.document.file_name}?</AlertDialogTitle>
          <AlertDialogDescription>
            It is no longer listed on this {item?.link.kind_title}. The document stays in the Library, and linking it
            again brings it back. The change is recorded in the Audit trail.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <GeneralError error={unlink.error} />
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            disabled={unlink.isPending}
            onClick={(e) => {
              e.preventDefault(); // stay open until the server accepts, so errors show here
              if (item) unlink.mutate(item.link.id);
            }}
          >
            Unlink document
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function LinkExistingDialog({
  kind,
  id,
  open,
  onOpenChange,
  linked,
}: {
  kind: string;
  id: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  linked: Set<number>;
}) {
  const refresh = useRefreshLibrary();
  const [words, setWords] = useState("");
  const found = useQuery({
    queryKey: ["library-documents", "picker", words.trim()],
    queryFn: () => fetchLibraryDocuments({ q: words.trim() || undefined }, 20),
    enabled: open,
  });
  const link = useMutation({
    mutationFn: (documentId: number) => linkLibraryDocument(documentId, kind, id),
    onSuccess: () => {
      refresh();
      onOpenChange(false);
    },
  });
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) {
          link.reset();
          setWords("");
        }
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Link an existing document</DialogTitle>
          <DialogDescription>
            Choose a document already in the Library. Search finds words in its name and in its text.
          </DialogDescription>
        </DialogHeader>
        <input
          type="search"
          className={inputClass}
          aria-label="Search the Library"
          placeholder="File name or words in it"
          value={words}
          onChange={(e) => setWords(e.target.value)}
        />
        <GeneralError error={link.error} />
        {found.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
        {found.error && <p className="text-sm text-destructive">Could not search: {found.error.message}</p>}
        {found.data?.items.length === 0 && (
          <p className="text-sm text-muted-foreground">
            {words.trim() ? "No document matches." : "The Library has no documents yet."}
          </p>
        )}
        {found.data && found.data.items.length > 0 && (
          <ul aria-label="Library documents" className="flex max-h-72 flex-col divide-y overflow-y-auto">
            {found.data.items.map((doc) => (
              <li key={doc.id} className="flex items-center justify-between gap-2 py-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{doc.file_name}</p>
                  <p className="text-xs text-muted-foreground">
                    {mediaTypeName(doc.media_type)} · {fileSizeText(doc.size_bytes)} · added{" "}
                    {doc.added_at.slice(0, 10)}
                  </p>
                </div>
                {linked.has(doc.id) ? (
                  <Badge variant="info">Linked</Badge>
                ) : (
                  <Button
                    type="button"
                    variant="outline"
                    disabled={link.isPending}
                    aria-label={`Link ${doc.file_name}`}
                    onClick={() => link.mutate(doc.id)}
                  >
                    Link
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * A record's Documents (ADR-0026 section 6): the Library documents linked to the record of this
 * `kind` and `id`. Add files one (filing it in the Library and linking it), link an existing
 * document, open it, and unlink it (the document stays in the Library). Mount it with one line on
 * any screen whose record kind is registered.
 */
export function LinkedDocuments({ kind, id }: { kind: string; id: number }) {
  const documents = useLinkedDocuments(kind, id);
  const health = useQuery({
    queryKey: ["library-check", kind, id],
    queryFn: () => checkLibrary({ kind, id }),
    enabled: !!documents.data?.length,
  });
  const refresh = useRefreshLibrary();
  const input = useRef<HTMLInputElement>(null);
  const [unlinking, setUnlinking] = useState<LibraryLinkedDocument | null>(null);
  const [picking, setPicking] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const add = useMutation({
    mutationFn: (file: File) => addAndLink(file, kind, id),
    onMutate: () => setNote(null),
    onSuccess: (added, file) => {
      refresh();
      setNote(
        added.already_in_library
          ? `'${file.name}' was already in the Library as '${added.document.file_name}', so that document is linked here.`
          : null,
      );
    },
  });
  const problems = new Map(health.data?.problems.map((p) => [p.document_id, p.problem]));
  const linkedIds = new Set(documents.data?.map((d) => d.document.id));

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle>Documents</CardTitle>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setPicking(true)}>
            Link existing
          </Button>
          <Button variant="outline" disabled={add.isPending} onClick={() => input.current?.click()}>
            <Paperclip size={16} aria-hidden />
            {add.isPending ? "Adding…" : "Add"}
          </Button>
        </div>
        <input
          ref={input}
          type="file"
          className="hidden"
          aria-label="Choose a file to add"
          accept={ACCEPT}
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = ""; // so choosing the same file again still fires
            if (file) add.mutate(file);
          }}
        />
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <FieldMessage error={add.error} field="file" />
        <GeneralError error={add.error} />
        {note && (
          <p role="status" className="text-sm text-muted-foreground">
            {note}
          </p>
        )}
        {documents.isPending && <p className="text-muted-foreground">Loading…</p>}
        {documents.error && (
          <p className="text-destructive">Could not load the documents: {documents.error.message}</p>
        )}
        {documents.data?.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No documents linked. Add a receipt or statement (PDF, PNG, JPEG, text or CSV, up to 10 MB), or link one
            that is already in the Library.
          </p>
        )}
        {documents.data && documents.data.length > 0 && (
          <ul className="flex flex-col divide-y" aria-label="Linked documents">
            {documents.data.map((item) => {
              const doc = item.document;
              const problem = problems.get(doc.id);
              return (
                <li key={item.link.id} className="flex items-center justify-between gap-2 py-2">
                  <div className="min-w-0">
                    <Link
                      to="/library/$documentId"
                      params={{ documentId: String(doc.id) }}
                      className="block truncate font-medium text-primary underline underline-offset-4"
                    >
                      {doc.file_name}
                    </Link>
                    <p className="text-xs text-muted-foreground">
                      {mediaTypeName(doc.media_type)} · {fileSizeText(doc.size_bytes)} · added{" "}
                      {doc.added_at.slice(0, 10)}
                    </p>
                    {problem && (
                      <p className="mt-1">
                        <Badge variant="flagged">{PROBLEM_TEXT[problem] ?? problem}</Badge>
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {!problem && (
                      <a
                        href={libraryFileUrl(doc.id)}
                        target="_blank"
                        rel="noreferrer"
                        aria-label={`Open ${doc.file_name}`}
                        className="text-sm text-primary underline underline-offset-4"
                      >
                        Open
                      </a>
                    )}
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="outline" aria-label={`Actions for ${doc.file_name}`} className="px-2">
                          <MoreHorizontal size={16} aria-hidden />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent>
                        {!problem && (
                          <DropdownMenuItem asChild>
                            <a href={libraryFileUrl(doc.id, true)}>Download</a>
                          </DropdownMenuItem>
                        )}
                        <DropdownMenuItem destructive onSelect={() => setUnlinking(item)}>
                          Unlink document
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {health.data && health.data.problems.length > 0 && (
          <p className="text-sm text-muted-foreground">
            A missing or changed file is not served. Add a missing file again, or restore it from a backup. A changed
            file is never overwritten, so it needs a backup.
          </p>
        )}
      </CardContent>
      <UnlinkDialog item={unlinking} onClose={() => setUnlinking(null)} />
      <LinkExistingDialog kind={kind} id={id} open={picking} onOpenChange={setPicking} linked={linkedIds} />
    </Card>
  );
}
