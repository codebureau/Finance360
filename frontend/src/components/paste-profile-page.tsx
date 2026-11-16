import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { useState } from "react";

import { DocumentFrame } from "@/components/document-frame";
import { ReadingView } from "@/components/document-profile-parts";
import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
import { PrefillLayout } from "@/components/prefill-layout";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { PROFILES_KEY, saveProfile, testProfile, type ProfileTest } from "@/lib/document-profiles";
import { fetchLibraryDocuments, libraryFileUrl } from "@/lib/library";

/**
 * Paste a Document profile as JSON, try it on a Library document beside that document, and save it
 * as a Proposed profile (ADR-0027, ADR-0026 section 7). Testing stores nothing; the profile is
 * switched on only from its own page.
 */
export function PasteProfilePage() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [json, setJson] = useState("");
  const [documentId, setDocumentId] = useState<number | null>(null);
  const [result, setResult] = useState<ProfileTest | null>(null);
  const documents = useQuery({
    queryKey: ["library", "profile-test-documents"],
    queryFn: () => fetchLibraryDocuments({}, 200),
  });
  // Only documents with a text layer can be read by a profile.
  const readable = (documents.data?.items ?? []).filter((d) => d.text_status === "extracted");

  const test = useMutation({
    mutationFn: () => testProfile(json, documentId as number),
    onSuccess: setResult,
    onMutate: () => setResult(null),
  });
  const save = useMutation({
    mutationFn: () => saveProfile(json, documentId as number),
    onSuccess: (saved) => {
      void client.invalidateQueries({ queryKey: PROFILES_KEY });
      void navigate({ to: "/settings/document-profiles/$profileId", params: { profileId: String(saved.id) } });
    },
  });
  const ready = json.trim() !== "" && documentId !== null;
  const acceptable = result?.acceptable === true;
  // A change to the draft or the document makes an earlier test stale.
  const stale = () => {
    setResult(null);
    test.reset();
    save.reset();
  };

  const form = (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) test.mutate();
      }}
    >
      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium">Profile JSON</span>
        <textarea
          className={`${inputClass} min-h-48 py-2 font-mono text-xs`}
          value={json}
          spellCheck={false}
          onChange={(e) => {
            setJson(e.target.value);
            stale();
          }}
        />
      </label>
      <FieldMessage error={test.error ?? save.error} field="profile" />
      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium">Library document to try it on</span>
        <select
          className={inputClass}
          value={documentId ?? ""}
          onChange={(e) => {
            setDocumentId(e.target.value ? Number(e.target.value) : null);
            stale();
          }}
        >
          <option value="">Choose a document&hellip;</option>
          {readable.map((d) => (
            <option key={d.id} value={d.id}>
              {d.file_name}
            </option>
          ))}
        </select>
      </label>
      {documents.data && readable.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No document with readable text is in the Library yet.{" "}
          <Link to="/library" search={{}} className="text-primary underline underline-offset-4">
            Add one in the Library
          </Link>
          .
        </p>
      )}
      <FieldMessage error={test.error ?? save.error} field="document" />
      <GeneralError error={test.error ?? save.error} />
      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant={acceptable ? "outline" : "default"} disabled={!ready || test.isPending}>
          {test.isPending ? "Testing…" : "Test profile"}
        </Button>
        {acceptable && (
          <Button disabled={save.isPending} onClick={() => save.mutate()}>
            Save as proposed profile
          </Button>
        )}
      </div>
      {result && (
        <section aria-label="Test result" className="flex flex-col gap-3 rounded-md border p-3">
          <p className="text-sm font-medium">
            {result.profile.title} · <span className="font-mono text-xs">{result.profile.key}</span> · v
            {result.profile.version} for {result.profile.target_title}
          </p>
          <ReadingView reading={result.reading} target={result.profile.target} />
          {result.profile.overrides_shipped && (
            <p className="text-sm text-muted-foreground">
              A shipped profile has this key. Once activated, this one replaces it.
            </p>
          )}
          {result.also_claimed_by.length > 0 && (
            <p role="status" className="text-sm text-warning">
              {result.also_claimed_by.join(", ")} also claims this document. The first by key reads it.
            </p>
          )}
          {!acceptable && (
            <p role="status" className="text-sm">
              It does not read this document yet, so it cannot be saved. Adjust the labels and test again.
            </p>
          )}
          {acceptable && (
            <p className="text-sm text-muted-foreground">
              Saving keeps the profile and a note of what it found (never a figure). It stays off until you activate
              it.
            </p>
          )}
        </section>
      )}
    </form>
  );

  return (
    <div className="flex flex-col gap-1">
      <Link
        to="/settings/document-profiles"
        className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft size={14} aria-hidden />
        Document profiles
      </Link>
      <PageHeader
        title="Paste a profile"
        description="Paste a Document profile as JSON and try it on a document in the Library. Nothing is saved until you choose to."
      />
      <div className="flex min-h-[32rem] flex-col lg:h-[calc(100dvh-13rem)]">
        <PrefillLayout
          form={form}
          document={
            documentId !== null ? (
              <DocumentFrame
                src={libraryFileUrl(documentId)}
                title={readable.find((d) => d.id === documentId)?.file_name ?? "The chosen document"}
              />
            ) : null
          }
        />
      </div>
    </div>
  );
}
