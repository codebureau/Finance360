import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Money } from "@/components/ui/money";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { Reading } from "@/lib/documents";
import {
  fieldName,
  NOT_FOUND_TEXT,
  STATUS,
  type ProfileDetail,
  type ProfileSummary,
} from "@/lib/document-profiles";

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, variant: "info" as const };
  return <Badge variant={s.variant}>{s.label}</Badge>;
}

export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm break-words">{children}</dd>
    </div>
  );
}

/** What a profile fills and checks: its fields (with labels) and the sums that must agree. */
export function ProfileDefinition({ profile }: { profile: ProfileDetail }) {
  const name = (key: string) => fieldName(profile.target, key);
  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Fields it fills</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-sm text-muted-foreground">
            A document is claimed when it contains every one of: {profile.claim.map((c) => `"${c}"`).join(", ")}.
          </p>
          <Table aria-label="Fields it fills">
            <TableHeader>
              <TableRow>
                <TableHead>Field</TableHead>
                <TableHead>Looks for</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {profile.fields.map((f) => (
                <TableRow key={f.key}>
                  <TableCell className="align-top">
                    {name(f.key)}
                    {f.required && <span className="ml-1 text-xs text-muted-foreground">(required)</span>}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{f.labels.join(" / ")}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Checks</CardTitle>
        </CardHeader>
        <CardContent>
          {profile.sums.length === 0 ? (
            <p className="text-sm text-muted-foreground">No sums to check.</p>
          ) : (
            <ul aria-label="Checks" className="flex flex-col gap-1 text-sm">
              {profile.sums.map((s) => (
                <li key={s.total}>
                  {name(s.total)} must equal {s.plus.map(name).join(" + ")}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </>
  );
}

/** The profile's JSON, read-only, collapsed until asked for. */
export function JsonView({ json }: { json: string }) {
  return (
    <details className="rounded-md border p-3">
      <summary className="cursor-pointer text-sm font-medium">Show the JSON</summary>
      <pre
        aria-label="Profile JSON"
        className="mt-3 max-h-96 overflow-auto rounded-md border border-border bg-muted p-3 font-mono text-xs break-words whitespace-pre-wrap"
      >
        {json}
      </pre>
    </details>
  );
}

const CHECK_BADGE = { agree: "ok", disagree: "flagged", skipped: "closed" } as const;

/**
 * What a profile found in the document, live: the figures with their page, the figures it did not
 * find, and each check. A Reading is never stored; these values are on screen only.
 */
export function ReadingView({ reading, target }: { reading: Reading; target: string }) {
  const name = (key: string) => fieldName(target, key);
  return (
    <div className="flex flex-col gap-3" data-testid="reading">
      <p className="flex items-center gap-2 text-sm">
        <Badge variant={reading.outcome === "ok" ? "ok" : reading.outcome === "warnings" ? "flagged" : "closed"}>
          {OUTCOME_TEXT[reading.outcome] ?? reading.outcome}
        </Badge>
        {OUTCOME_HELP[reading.outcome]}
      </p>
      {reading.found.length > 0 && (
        <Table aria-label="Found in the document">
          <TableHeader>
            <TableRow>
              <TableHead>Field</TableHead>
              <TableHead className="text-right">Found</TableHead>
              <TableHead className="w-16 text-right">Page</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {reading.found.map((f) => (
              <TableRow key={f.key}>
                <TableCell>{name(f.key)}</TableCell>
                <TableCell className="text-right">
                  {f.kind === "money" ? (
                    <Money amount={f.value} />
                  ) : f.kind === "fy" ? (
                    `Year ending ${f.value}`
                  ) : (
                    f.value
                  )}
                  {f.how === "absent" && <span className="ml-1 text-xs text-muted-foreground">(not stated: 0)</span>}
                </TableCell>
                <TableCell className="text-right text-muted-foreground">{f.page > 0 ? f.page : "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {reading.not_found.length > 0 && (
        <ul aria-label="Not found" className="flex flex-col gap-1 text-sm">
          {reading.not_found.map((n) => (
            <li key={n.key}>
              <span className="font-medium">{name(n.key)}</span>
              {n.required && <span className="text-muted-foreground"> (required)</span>} not found:{" "}
              {NOT_FOUND_TEXT[n.reason] ?? n.reason}.
            </li>
          ))}
        </ul>
      )}
      {reading.checks.length > 0 && (
        <ul aria-label="Check results" className="flex flex-col gap-1 text-sm">
          {reading.checks.map((c, i) => (
            <li key={`${c.kind}-${i}`} className="flex flex-wrap items-center gap-2">
              <Badge variant={CHECK_BADGE[c.status as keyof typeof CHECK_BADGE] ?? "info"}>
                {c.status === "agree" ? "Agrees" : c.status === "disagree" ? "Disagrees" : "Not checked"}
              </Badge>
              {c.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const OUTCOME_TEXT: Record<string, string> = {
  ok: "Reads",
  warnings: "Reads, with warnings",
  needs_repair: "Needs repair",
  unrecognised: "Not recognised",
};

const OUTCOME_HELP: Record<string, string> = {
  ok: "Every figure was found and every check agrees.",
  warnings: "Every required figure was found; a check disagrees or an optional figure is missing.",
  needs_repair: "The document is claimed, but a required figure was not found.",
  unrecognised: "The document does not contain the phrases this profile claims it by.",
};

/** The one-line facts of a profile: its form, source and version. */
export function ProfileFacts({ profile }: { profile: ProfileSummary }) {
  return (
    <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <Fact label="Fills">{profile.target_title}</Fact>
      <Fact label="Version">{profile.version}</Fact>
      <Fact label="Key">
        <span className="font-mono text-xs">{profile.key}</span>
      </Fact>
      <Fact label="Status">
        <StatusBadge status={profile.status} />
      </Fact>
    </dl>
  );
}
