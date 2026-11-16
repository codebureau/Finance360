import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowLeft, MoreHorizontal } from "lucide-react";
import { useState } from "react";

import { DocumentFrame } from "@/components/document-frame";
import {
  Fact,
  JsonView,
  ProfileDefinition,
  ProfileFacts,
  ReadingView,
  StatusBadge,
} from "@/components/document-profile-parts";
import { FieldMessage, GeneralError } from "@/components/form-fields";
import { PrefillLayout } from "@/components/prefill-layout";
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
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { PageHeader } from "@/components/ui/page-header";
import {
  activateProfile,
  fetchProfile,
  fetchShippedProfile,
  PROFILES_KEY,
  retireProfile,
  reviewProfile,
  SOURCE_TEXT,
  type ProfileDetail,
} from "@/lib/document-profiles";
import { libraryFileUrl } from "@/lib/library";

function BackLink() {
  return (
    <Link
      to="/settings/document-profiles"
      className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft size={14} aria-hidden />
      Document profiles
    </Link>
  );
}

/** Errors a profile action can return, beside the action. */
function Problems({ error }: { error: unknown }) {
  return (
    <div role="alert" className="flex flex-col gap-1">
      <FieldMessage error={error} field="profile" />
      <FieldMessage error={error} field="document" />
      <FieldMessage error={error} field="status" />
      <GeneralError error={error} />
    </div>
  );
}

function RetireDialog({
  profile,
  open,
  onOpenChange,
  onDone,
}: {
  profile: ProfileDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDone: () => void;
}) {
  const retire = useMutation({
    mutationFn: () => retireProfile(profile.id as number),
    onSuccess: () => {
      onOpenChange(false);
      onDone();
    },
  });
  const active = profile.status === "active";
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) retire.reset();
        onOpenChange(next);
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            Retire {profile.title} v{profile.version}?
          </AlertDialogTitle>
          <AlertDialogDescription>
            {active
              ? "It stops reading documents straight away. If a shipped profile has the same key, that one reads again. "
              : "It is never switched on. "}
            A retired profile cannot be activated again; paste a new version instead. The change is recorded in the
            Audit trail.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <Problems error={retire.error} />
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            disabled={retire.isPending}
            onClick={(e) => {
              e.preventDefault(); // stay open until the server accepts, so errors show here
              retire.mutate();
            }}
          >
            Retire profile
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function SavedProfile({ profile }: { profile: ProfileDetail }) {
  const id = profile.id as number;
  const client = useQueryClient();
  const [retiring, setRetiring] = useState(false);
  const [alsoClaimedBy, setAlsoClaimedBy] = useState<string[] | null>(null);
  const live = profile.status === "proposed" || profile.status === "active";
  const review = useQuery({
    queryKey: [...PROFILES_KEY, id, "review"],
    queryFn: () => reviewProfile(id),
    enabled: live && profile.test_document !== null,
    retry: false,
  });
  const refresh = () => {
    void client.invalidateQueries({ queryKey: PROFILES_KEY });
  };
  const activate = useMutation({
    mutationFn: () => activateProfile(id),
    onSuccess: (result) => {
      setAlsoClaimedBy(result.also_claimed_by);
      refresh();
    },
  });
  const claimed = alsoClaimedBy ?? review.data?.also_claimed_by ?? [];
  const canActivate = profile.status === "proposed" && review.data?.acceptable === true;
  const snapshot = profile.test_snapshot;
  const found = snapshot?.fields.filter((f) => f.state === "found" || f.state === "absent").length ?? 0;

  const form = (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>Details</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <ProfileFacts profile={profile} />
          <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Fact label="Source">{SOURCE_TEXT[profile.source] ?? profile.source}</Fact>
            <Fact label="Saved by">{profile.proposed_by}</Fact>
            <Fact label="Saved">{profile.created_at?.slice(0, 10)}</Fact>
            {profile.activated_at && <Fact label="Activated">{profile.activated_at.slice(0, 10)}</Fact>}
            {profile.ended_at && (
              <Fact label="Retired">
                {profile.ended_at.slice(0, 10)}
                {profile.ended_reason === "replaced" ? " (replaced by a newer version)" : ""}
              </Fact>
            )}
          </dl>
          {profile.failure && (
            <p role="alert" className="text-sm text-warning">
              Failed to load: {profile.failure} It is not reading documents. Retire it, then paste a corrected version.
            </p>
          )}
          {profile.overrides_shipped && (
            <p className="text-sm text-muted-foreground">
              It replaces the shipped profile with the same key while it is active.
            </p>
          )}
          <p>
            <Link
              to="/audit"
              search={{ entity_type: "document_profile", entity_id: id }}
              className="text-sm text-primary underline underline-offset-4"
            >
              History in the Audit trail
            </Link>
          </p>
        </CardContent>
      </Card>

      {live && (
        <Card>
          <CardHeader>
            <CardTitle>Reading, live</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {profile.test_document === null ? (
              <p role="alert" className="text-sm text-warning">
                The document this profile was tested on is no longer in the Library, so it cannot be tested again.
                {profile.status === "proposed" ? " It cannot be activated; retire it." : ""}
              </p>
            ) : (
              <>
                <p className="text-sm text-muted-foreground">
                  The profile is run over {profile.test_document.file_name} now, beside the document. Nothing from this
                  reading is stored.
                </p>
                {review.isPending && <p className="text-sm text-muted-foreground">Reading&hellip;</p>}
                {review.isError && <Problems error={review.error} />}
                {review.data && <ReadingView reading={review.data.reading} target={profile.target} />}
              </>
            )}
            {claimed.length > 0 && (
              <p role="status" className="text-sm text-warning" data-testid="double-claim">
                Another profile for this form also claims this document: {claimed.join(", ")}. When more than one
                claims a document, the first by key reads it.
              </p>
            )}
            {alsoClaimedBy !== null && (
              <p role="status" className="text-sm">
                Activated. It reads documents from now on.
              </p>
            )}
            <Problems error={activate.error} />
          </CardContent>
        </Card>
      )}

      {snapshot && (
        <Card>
          <CardHeader>
            <CardTitle>Test when saved</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            Outcome {snapshot.outcome}: {found} {found === 1 ? "field" : "fields"} found,{" "}
            {snapshot.fields.length - found} not found. Only this is kept, never a figure from the document.
          </CardContent>
        </Card>
      )}

      <ProfileDefinition profile={profile} />
      <JsonView json={profile.profile_json} />
    </div>
  );

  return (
    <div className="flex flex-col gap-1">
      <BackLink />
      <PageHeader
        title={profile.title}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs">
              {profile.key} · v{profile.version}
            </span>
            <StatusBadge status={profile.status} />
          </span>
        }
        action={
          live && (
            <div className="flex items-center gap-2">
              {profile.status === "proposed" && (
                <Button disabled={!canActivate || activate.isPending} onClick={() => activate.mutate()}>
                  Activate profile
                </Button>
              )}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" aria-label="More actions" className="px-2">
                    <MoreHorizontal size={16} aria-hidden />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent>
                  <DropdownMenuItem destructive onSelect={() => setRetiring(true)}>
                    Retire profile
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          )
        }
      />
      <div className="flex min-h-[32rem] flex-col lg:h-[calc(100dvh-13rem)]">
        <PrefillLayout
          form={form}
          document={
            profile.test_document ? (
              <DocumentFrame src={libraryFileUrl(profile.test_document.id)} title={profile.test_document.file_name} />
            ) : null
          }
        />
      </div>
      <RetireDialog profile={profile} open={retiring} onOpenChange={setRetiring} onDone={refresh} />
    </div>
  );
}

/** A saved profile at `/settings/document-profiles/$profileId`. */
export function DocumentProfilePage({ profileId }: { profileId: string }) {
  const id = Number(profileId);
  const profile = useQuery({
    queryKey: [...PROFILES_KEY, id],
    queryFn: () => fetchProfile(id),
    enabled: Number.isInteger(id),
  });
  if (profile.isPending)
    return (
      <div className="flex flex-col gap-1">
        <BackLink />
        <p className="text-sm text-muted-foreground">Loading&hellip;</p>
      </div>
    );
  if (profile.isError || !profile.data)
    return (
      <div className="flex flex-col gap-1">
        <BackLink />
        <p role="alert" className="text-sm text-destructive">
          Couldn&apos;t load this Document profile.
        </p>
      </div>
    );
  return <SavedProfile profile={profile.data} />;
}

/** A shipped profile at `/settings/document-profiles/shipped/$key`: read-only. */
export function ShippedProfilePage({ profileKey }: { profileKey: string }) {
  const profile = useQuery({
    queryKey: [...PROFILES_KEY, "shipped", profileKey],
    queryFn: () => fetchShippedProfile(profileKey),
  });
  if (profile.isPending)
    return (
      <div className="flex flex-col gap-1">
        <BackLink />
        <p className="text-sm text-muted-foreground">Loading&hellip;</p>
      </div>
    );
  if (profile.isError || !profile.data)
    return (
      <div className="flex flex-col gap-1">
        <BackLink />
        <p role="alert" className="text-sm text-destructive">
          Couldn&apos;t load this Document profile.
        </p>
      </div>
    );
  const p = profile.data;
  return (
    <div className="flex max-w-3xl flex-col gap-4">
      <BackLink />
      <PageHeader
        title={p.title}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs">
              {p.key} · v{p.version}
            </span>
            <StatusBadge status={p.status} />
          </span>
        }
      />
      <Card>
        <CardHeader>
          <CardTitle>Details</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <ProfileFacts profile={p} />
          <p className="text-sm text-muted-foreground">
            This profile ships with Finance360 and is read-only. It changes only in a release.
            {p.overridden ? " A saved profile with the same key replaces it while that one is active." : ""}
          </p>
        </CardContent>
      </Card>
      <ProfileDefinition profile={p} />
      <JsonView json={p.profile_json} />
    </div>
  );
}
