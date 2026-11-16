import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { inputClass } from "@/components/form-fields";
import { useRefreshLibrary } from "@/components/linked-documents";
import { LibraryProblems } from "@/components/library-problems";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  ACCEPT,
  addLibraryDocument,
  fetchLibraryDocuments,
  fetchLibraryKinds,
  fileSizeText,
  MEDIA_TYPE_NAMES,
  mediaTypeName,
  PAGE_SIZE,
  type LibraryDocument,
  type LibrarySearch,
} from "@/lib/library";

/** The "Linked to" choices: anything, nothing (unlinked), or a kind of record. */
const UNLINKED = "unlinked";

type Outcome = { added: string[]; already: string[]; failed: { name: string; message: string }[] };

function DocumentRow({ doc }: { doc: LibraryDocument }) {
  const navigate = useNavigate();
  return (
    <TableRow
      className="cursor-pointer"
      onClick={(e) => {
        // The name is a real link (keyboard, middle-click); let it handle its own click.
        if (!(e.target as HTMLElement).closest("a")) {
          void navigate({ to: "/library/$documentId", params: { documentId: String(doc.id) } });
        }
      }}
    >
      <TableCell className="max-w-0">
        <Link
          to="/library/$documentId"
          params={{ documentId: String(doc.id) }}
          className="block truncate font-medium hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          {doc.file_name}
        </Link>
        <p className="text-xs text-muted-foreground sm:hidden">
          {mediaTypeName(doc.media_type)} · {fileSizeText(doc.size_bytes)} · {doc.added_at.slice(0, 10)}
        </p>
      </TableCell>
      <TableCell className="hidden whitespace-nowrap sm:table-cell">{mediaTypeName(doc.media_type)}</TableCell>
      <TableCell className="hidden whitespace-nowrap sm:table-cell">{fileSizeText(doc.size_bytes)}</TableCell>
      <TableCell className="hidden whitespace-nowrap sm:table-cell">{doc.added_at.slice(0, 10)}</TableCell>
      <TableCell className="text-right whitespace-nowrap">
        {doc.link_count === 0 ? (
          <Badge variant="info">Unlinked</Badge>
        ) : (
          `${doc.link_count} ${doc.link_count === 1 ? "record" : "records"}`
        )}
      </TableCell>
    </TableRow>
  );
}

/**
 * The Library at `/library` (ADR-0026 section 6): every stored document, with search over its name
 * and text, filters for type, what it is linked to and "unlinked", and Add. The filters and the
 * page are in the URL.
 */
export function LibraryPage({
  search,
  onSearch,
}: {
  search: LibrarySearch;
  onSearch: (patch: Partial<LibrarySearch>) => void;
}) {
  const documents = useQuery({
    queryKey: ["library-documents", "list", search],
    queryFn: () => fetchLibraryDocuments(search),
  });
  const kinds = useQuery({ queryKey: ["library-kinds"], queryFn: fetchLibraryKinds });
  const refresh = useRefreshLibrary();
  const input = useRef<HTMLInputElement>(null);
  // The box keeps what was typed (a trailing space included); the URL keeps it trimmed.
  const [words, setWords] = useState(search.q ?? "");
  useEffect(() => {
    setWords((typed) => ((search.q ?? "") === typed.trim() ? typed : (search.q ?? "")));
  }, [search.q]);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const add = useMutation({
    mutationFn: async (files: File[]): Promise<Outcome> => {
      const result: Outcome = { added: [], already: [], failed: [] };
      for (const file of files) {
        try {
          const done = await addLibraryDocument(file);
          (done.already_in_library ? result.already : result.added).push(file.name);
        } catch (e) {
          result.failed.push({ name: file.name, message: e instanceof Error ? e.message : "Could not be added." });
        }
      }
      return result;
    },
    onMutate: () => setOutcome(null),
    onSuccess: (result) => {
      setOutcome(result);
      refresh();
    },
  });

  const narrowed = !!(search.q || search.type || search.kind || search.unlinked);
  const total = documents.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const page = search.page ?? 1;
  const linkedTo = search.unlinked ? UNLINKED : (search.kind ?? "");

  return (
    <div className="flex max-w-5xl flex-col gap-4">
      <PageHeader
        title="Library"
        description="Every document you have kept: statements, receipts and notices. Add them here, or from the record they support."
        action={
          <>
            <Button disabled={add.isPending} onClick={() => input.current?.click()}>
              <Plus size={16} aria-hidden />
              {add.isPending ? "Adding…" : "Add documents"}
            </Button>
            <input
              ref={input}
              type="file"
              multiple
              className="hidden"
              aria-label="Choose files to add"
              accept={ACCEPT}
              onChange={(e) => {
                const files = Array.from(e.target.files ?? []);
                e.target.value = ""; // so choosing the same file again still fires
                if (files.length > 0) add.mutate(files);
              }}
            />
          </>
        }
      />

      {outcome && (
        <div role="status" className="flex flex-col gap-1 rounded-md border p-3 text-sm">
          {outcome.added.length > 0 && (
            <p>
              Added {outcome.added.length === 1 ? `'${outcome.added[0]}'` : `${outcome.added.length} documents`} to the
              Library.
            </p>
          )}
          {outcome.already.length > 0 && (
            <p>
              {outcome.already.length === 1 ? `'${outcome.already[0]}' was` : `${outcome.already.length} documents were`}{" "}
              already in the Library, so nothing new was stored.
            </p>
          )}
          {outcome.failed.map((f) => (
            <p key={f.name} className="text-destructive">
              '{f.name}' was not added: {f.message}
            </p>
          ))}
        </div>
      )}

      <LibraryProblems />

      <form
        role="search"
        aria-label="Filter the Library"
        className="grid grid-cols-1 gap-3 sm:grid-cols-3"
        onSubmit={(e) => e.preventDefault()}
      >
        <label className="flex min-w-0 flex-col gap-1 text-sm sm:col-span-3 lg:col-span-1">
          Search
          <input
            type="search"
            className={inputClass}
            placeholder="File name or words in it"
            value={words}
            onChange={(e) => {
              setWords(e.target.value);
              onSearch({ q: e.target.value.trim() || undefined, page: undefined });
            }}
          />
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-sm">
          Type
          <select
            className={inputClass}
            value={search.type ?? ""}
            onChange={(e) => onSearch({ type: e.target.value || undefined, page: undefined })}
          >
            <option value="">All types</option>
            {MEDIA_TYPE_NAMES.map((m) => (
              <option key={m.value} value={m.value}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-sm">
          Linked to
          <select
            className={inputClass}
            value={linkedTo}
            onChange={(e) => {
              const value = e.target.value;
              onSearch({
                unlinked: value === UNLINKED ? true : undefined,
                kind: value && value !== UNLINKED ? value : undefined,
                page: undefined,
              });
            }}
          >
            <option value="">Anything or nothing</option>
            <option value={UNLINKED}>Unlinked (nothing)</option>
            {kinds.data?.map((k) => (
              <option key={k.key} value={k.key}>
                {k.title}
              </option>
            ))}
          </select>
        </label>
      </form>

      {documents.isPending && <p className="text-muted-foreground">Loading…</p>}
      {documents.error && (
        <p className="text-destructive">Could not load the Library: {documents.error.message}</p>
      )}
      {documents.data && (
        <p className="text-sm text-muted-foreground" data-testid="library-count">
          {total === 0
            ? narrowed
              ? "No document matches these filters."
              : "The Library is empty. Add a statement, receipt or notice and it is kept here."
            : `${total} ${total === 1 ? "document" : "documents"}${narrowed ? " match" : ""}`}
        </p>
      )}
      {documents.data && documents.data.items.length > 0 && (
        <Table aria-label="Library documents" className="table-fixed">
          <TableHeader>
            <TableRow>
              <TableHead>Document</TableHead>
              <TableHead className="hidden sm:table-cell">Type</TableHead>
              <TableHead className="hidden sm:table-cell">Size</TableHead>
              <TableHead className="hidden sm:table-cell">Added</TableHead>
              <TableHead className="w-28 text-right">Linked to</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {documents.data.items.map((doc) => (
              <DocumentRow key={doc.id} doc={doc} />
            ))}
          </TableBody>
        </Table>
      )}
      {pages > 1 && (
        <div className="flex items-center justify-between gap-2 text-sm">
          <Button variant="outline" disabled={page <= 1} onClick={() => onSearch({ page: page - 1 })}>
            Previous
          </Button>
          <span className="text-muted-foreground">
            Page {page} of {pages}
          </span>
          <Button variant="outline" disabled={page >= pages} onClick={() => onSearch({ page: page + 1 })}>
            Next
          </Button>
        </div>
      )}
    </div>
  );
}
