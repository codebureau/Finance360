import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";

import { StatusBadge } from "@/components/document-profile-parts";
import { buttonVariants } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fetchProfiles, fieldName, PROFILES_KEY, SOURCE_TEXT, type ProfileSummary } from "@/lib/document-profiles";

function ProfileLink({ profile }: { profile: ProfileSummary }) {
  const className = "font-medium text-primary underline underline-offset-4";
  const title = (
    <>
      {profile.title}
      <span className="block font-mono text-xs font-normal text-muted-foreground no-underline">
        {profile.key} · v{profile.version}
      </span>
    </>
  );
  return profile.id === null ? (
    <Link to="/settings/document-profiles/shipped/$key" params={{ key: profile.key }} className={className}>
      {title}
    </Link>
  ) : (
    <Link to="/settings/document-profiles/$profileId" params={{ profileId: String(profile.id) }} className={className}>
      {title}
    </Link>
  );
}

function ProfileRow({ profile }: { profile: ProfileSummary }) {
  const navigate = useNavigate();
  const names = profile.fills.map((k) => fieldName(profile.target, k));
  return (
    <TableRow
      className="cursor-pointer"
      onClick={(e) => {
        // The title is a real link (keyboard, middle-click); let it handle its own click.
        if ((e.target as HTMLElement).closest("a")) return;
        if (profile.id === null) {
          void navigate({ to: "/settings/document-profiles/shipped/$key", params: { key: profile.key } });
        } else {
          void navigate({
            to: "/settings/document-profiles/$profileId",
            params: { profileId: String(profile.id) },
          });
        }
      }}
    >
      <TableCell className="align-top">
        <ProfileLink profile={profile} />
        {profile.failure && (
          <p role="alert" className="mt-1 text-xs text-warning">
            {profile.failure}
          </p>
        )}
        {profile.overrides_shipped && (
          <p className="mt-1 text-xs text-muted-foreground">Replaces the shipped profile with this key.</p>
        )}
        {profile.overridden && (
          <p className="mt-1 text-xs text-muted-foreground">A saved profile replaces this one while it is active.</p>
        )}
      </TableCell>
      <TableCell className="hidden align-top sm:table-cell">
        <span className="block">{profile.target_title}</span>
        <span className="block text-xs text-muted-foreground">
          {names.length} {names.length === 1 ? "field" : "fields"} · {profile.sums.length}{" "}
          {profile.sums.length === 1 ? "check" : "checks"}
        </span>
      </TableCell>
      <TableCell className="hidden align-top md:table-cell">{SOURCE_TEXT[profile.source] ?? profile.source}</TableCell>
      <TableCell className="align-top">
        <StatusBadge status={profile.status} />
      </TableCell>
    </TableRow>
  );
}

/**
 * Settings → Document profiles (ADR-0027): every profile Finance360 can read a document with. The
 * ones that ship with the app are read-only; the ones saved here are proposed until activated.
 */
export function DocumentProfilesPage() {
  const profiles = useQuery({ queryKey: PROFILES_KEY, queryFn: fetchProfiles });
  return (
    <div className="flex flex-col gap-4">
      <Link to="/settings" className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft size={14} aria-hidden />
        Settings
      </Link>
      <PageHeader
        title="Document profiles"
        description={
          <>
            How Finance360 recognises a kind of statement and reads figures from it.{" "}
            <Link to="/guide/library" className="text-primary underline underline-offset-4">
              About Document profiles
            </Link>
          </>
        }
        action={
          <Link to="/settings/document-profiles/new" className={buttonVariants()}>
            Paste a profile
          </Link>
        }
      />
      {profiles.isPending && <p className="text-sm text-muted-foreground">Loading&hellip;</p>}
      {profiles.isError && (
        <p role="alert" className="text-sm text-destructive">
          Couldn&apos;t load the Document profiles.
        </p>
      )}
      {profiles.data && (
        <Table aria-label="Document profiles">
          <TableHeader>
            <TableRow>
              <TableHead>Profile</TableHead>
              <TableHead className="hidden sm:table-cell">Fills</TableHead>
              <TableHead className="hidden md:table-cell">Source</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {profiles.data.map((p) => (
              <ProfileRow key={`${p.id ?? "shipped"}-${p.key}-${p.version}`} profile={p} />
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
