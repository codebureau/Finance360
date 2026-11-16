import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, MoreHorizontal } from "lucide-react";
import { useRef, useState } from "react";

import { DocumentFrame } from "@/components/document-frame";
import { GeneralError } from "@/components/form-fields";
import { useRefreshLibrary } from "@/components/linked-documents";
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
import {
  checkLibrary,
  deleteLibraryDocument,
  fetchLibraryDocument,
  fileSizeText,
  libraryFileUrl,
  mediaTypeName,
  PROBLEM_TEXT,
  TEXT_STATUS_TEXT,
  unlinkLibraryDocument,
  type LibraryDocumentDetail,
  type LibraryLink,
} from "@/lib/library";
import { cn } from "@/lib/utils";

function BackLink() {
  return (
    <Link to="/library" search={{}} className={buttonVariants({ variant: "ghost" }) + " -ml-3 w-fit"}>
      <ArrowLeft size={16} aria-hidden />
      Library
    </Link>
  );
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm break-words">{children}</dd>
    </div>
  );
}

function UnlinkDialog({
  doc,
  link,
  onClose,
}: {
  doc: LibraryDocumentDetail;
  link: LibraryLink | null;
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
      open={link !== null}
      onOpenChange={(next) => {
        if (!next) {
          unlink.reset();
          onClose();
        }
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            Unlink {doc.file_name} from {link?.label}?
          </AlertDialogTitle>
          <AlertDialogDescription>
            It is no longer listed on this {link?.kind_title}. The document stays in the Library, and linking it again
            brings it back. The change is recorded in the Audit trail.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <GeneralError error={unlink.error} />
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            disabled={unlink.isPending}
            onClick={(e) => {
              e.preventDefault(); // stay open until the server accepts, so errors show here
              if (link) unlink.mutate(link.id);
            }}
          >
            Unlink document
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function DeleteDialog({
  doc,
  open,
  onOpenChange,
  onCloseAutoFocus,
}: {
  doc: LibraryDocumentDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCloseAutoFocus: (e: Event) => void;
}) {
  const navigate = useNavigate();
  const refresh = useRefreshLibrary();
  const remove = useMutation({
    mutationFn: () => deleteLibraryDocument(doc.id),
    onSuccess: () => {
      onOpenChange(false);
      refresh();
      void navigate({ to: "/library", search: {} });
    },
  });
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) remove.reset();
        onOpenChange(next);
      }}
    >
      <AlertDialogContent onCloseAutoFocus={onCloseAutoFocus}>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete {doc.file_name}?</AlertDialogTitle>
          <AlertDialogDescription>
            The document and its stored file are removed from the Library for good. It may be the only copy, so
            restore it from a backup if you need it back. The deletion is recorded in the Audit trail.
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
            Delete document
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function DocumentDetail({ doc }: { doc: LibraryDocumentDetail }) {
  const [unlinking, setUnlinking] = useState<LibraryLink | null>(null);
  const [deleting, setDeleting] = useState(false);
  const menuRef = useRef<HTMLButtonElement>(null);
  const health = useQuery({ queryKey: ["library-check", "document", doc.id], queryFn: () => checkLibrary() });
  const problem = health.data?.problems.find((p) => p.document_id === doc.id)?.problem;
  const linked = doc.links.length > 0;

  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <div className="flex flex-col gap-1">
        <BackLink />
        <PageHeader
          title={doc.file_name}
          description={`${mediaTypeName(doc.media_type)} · ${fileSizeText(doc.size_bytes)} · added ${doc.added_at.slice(0, 10)}`}
          action={
            <div className="flex items-center gap-2">
              {!problem && (
                <a
                  href={libraryFileUrl(doc.id)}
                  target="_blank"
                  rel="noreferrer"
                  className={buttonVariants({ variant: "outline" })}
                >
                  Open
                </a>
              )}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button ref={menuRef} variant="outline" aria-label="More actions" className="px-2">
                    <MoreHorizontal size={16} aria-hidden />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent>
                  {!problem && (
                    <DropdownMenuItem asChild>
                      <a href={libraryFileUrl(doc.id, true)}>Download</a>
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem destructive disabled={linked} onSelect={() => setDeleting(true)}>
                    Delete document
                  </DropdownMenuItem>
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
        <CardContent className="flex flex-col gap-3">
          {problem && (
            <p role="alert" className="flex flex-wrap items-center gap-2 text-sm">
              <Badge variant="flagged">{PROBLEM_TEXT[problem] ?? problem}</Badge>
              The file is not served. Add the same file again, or restore it from a backup. A changed file is never
              overwritten, so it needs a backup.
            </p>
          )}
          <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Detail label="Type">{mediaTypeName(doc.media_type)}</Detail>
            <Detail label="Size">{fileSizeText(doc.size_bytes)}</Detail>
            <Detail label="Added">{doc.added_at.slice(0, 10)}</Detail>
            <Detail label="Text">{TEXT_STATUS_TEXT[doc.text_status] ?? doc.text_status}</Detail>
          </dl>
          <p>
            <Link
              to="/audit"
              search={{ entity_type: "library_document", entity_id: doc.id }}
              className={cn(buttonVariants({ variant: "link" }), "h-auto px-0")}
            >
              History in the Audit trail
            </Link>
          </p>
        </CardContent>
      </Card>

      {!problem && (
        <Card>
          <CardHeader>
            <CardTitle>Document</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="h-[70vh] min-h-[24rem]">
              <DocumentFrame src={libraryFileUrl(doc.id)} title={doc.file_name} />
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Linked to</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {!linked && (
            <p className="text-sm text-muted-foreground">
              Not linked to any record. Open a record and choose Link existing to link it, or delete it if it is not
              needed.
            </p>
          )}
          {linked && (
            <ul aria-label="Records this document is linked to" className="flex flex-col divide-y">
              {doc.links.map((link) => (
                <li key={link.id} className="flex items-center justify-between gap-2 py-2">
                  <div className="min-w-0">
                    <p className="text-xs text-muted-foreground">{link.kind_title}</p>
                    {link.route ? (
                      <Link
                        to={link.route}
                        className="block truncate font-medium text-primary underline underline-offset-4"
                      >
                        {link.label}
                      </Link>
                    ) : (
                      <p className="truncate font-medium">{link.label}</p>
                    )}
                    <p className="text-xs text-muted-foreground">
                      linked {link.added_at.slice(0, 10)}
                      {link.removed && (
                        <>
                          {" "}
                          <Badge variant="closed">Removed</Badge>
                        </>
                      )}
                    </p>
                  </div>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="outline" aria-label={`Actions for ${link.label}`} className="px-2">
                        <MoreHorizontal size={16} aria-hidden />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent>
                      <DropdownMenuItem destructive onSelect={() => setUnlinking(link)}>
                        Unlink document
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </li>
              ))}
            </ul>
          )}
          {linked && (
            <p className="text-sm text-muted-foreground" data-testid="delete-hint">
              A document linked to a record cannot be deleted. Unlink it from{" "}
              {doc.links.length === 1 ? "that record" : "each of these records"} first.
            </p>
          )}
        </CardContent>
      </Card>

      <UnlinkDialog doc={doc} link={unlinking} onClose={() => setUnlinking(null)} />
      <DeleteDialog
        doc={doc}
        open={deleting}
        onOpenChange={setDeleting}
        onCloseAutoFocus={(e) => {
          e.preventDefault();
          menuRef.current?.focus();
        }}
      />
    </div>
  );
}

/** A document's view at `/library/$documentId`: the file's details and every record it is linked to. */
export function LibraryDocumentPage({ documentId }: { documentId: string }) {
  const id = Number(documentId);
  const document = useQuery({
    queryKey: ["library-document", id],
    queryFn: () => fetchLibraryDocument(id),
    enabled: Number.isInteger(id) && id > 0,
  });
  if (!Number.isInteger(id) || id <= 0 || document.error) {
    return (
      <div className="flex flex-col gap-2">
        <BackLink />
        <p role="alert" className="text-destructive">
          {document.error ? document.error.message : "There is no such document."}
        </p>
      </div>
    );
  }
  if (!document.data) return <p className="text-muted-foreground">Loading…</p>;
  return <DocumentDetail doc={document.data} />;
}
