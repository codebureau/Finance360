import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useState } from "react";

import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
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
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  fetchExposureProfiles,
  fetchExposureVocabulary,
  judgeTotal,
  ppmToPercent,
  recordExposureProfile,
  WEIGHT_TOLERANCE_PPM,
  type ExposureDimension,
  type ExposureProfile,
} from "@/lib/exposure";
import { fetchHousehold, todayIn } from "@/lib/household";
import type { Instrument } from "@/lib/instruments";

const DEFAULT_TIME_ZONE = "Australia/Melbourne";
/** Earlier versions shown before "Show all". */
const SHOWN = 5;

const labelName = (vocab: ExposureDimension[], dimension: string, label: string) =>
  vocab.find((d) => d.key === dimension)?.labels.find((l) => l.key === label)?.name ?? label;

/** How the running total of one Dimension will be judged (the server decides; this previews). */
function totalMessage(state: ReturnType<typeof judgeTotal>): string {
  const total = ppmToPercent(state.totalPpm);
  const gap = ppmToPercent(Math.abs(state.differencePpm));
  switch (state.state) {
    case "empty":
      return "Nothing entered: this Dimension is left out.";
    case "exact":
      return `Total ${total}%. Adds to 100%.`;
    case "short":
      return `Total ${total}%: ${gap} points short of 100%. The shortfall shows as Not classified.`;
    case "over":
      return `Total ${total}%: ${gap} points over 100%. The excess is trimmed from the largest weight.`;
    default:
      return `Total ${total}%: ${gap} points ${state.differencePpm < 0 ? "short of" : "over"} 100%. More than ${ppmToPercent(WEIGHT_TOLERANCE_PPM)} point either way is rejected.`;
  }
}

function ProfileForm({
  instrument,
  vocab,
  today,
  initial,
  onDone,
  onCancel,
}: {
  instrument: Instrument;
  vocab: ExposureDimension[];
  today: string;
  initial?: ExposureProfile;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [asAt, setAsAt] = useState(initial?.as_at ?? today);
  const [note, setNote] = useState(initial?.provenance_note ?? "");
  const [entered, setEntered] = useState<Record<string, string>>(() =>
    Object.fromEntries((initial?.weights ?? []).map((w) => [`${w.dimension_key}.${w.label_key}`, w.percent])),
  );
  const save = useMutation({
    mutationFn: () =>
      recordExposureProfile(instrument.id, {
        as_at: asAt,
        provenance_note: note,
        weights: vocab.flatMap((d) =>
          d.labels
            .filter((l) => (entered[`${d.key}.${l.key}`] ?? "").trim() !== "")
            .map((l) => ({
              dimension_key: d.key,
              label_key: l.key,
              percent: entered[`${d.key}.${l.key}`].trim(),
            })),
        ),
      }),
    onSuccess: onDone,
  });

  return (
    <form
      aria-label={initial ? `Correct exposure profile of ${instrument.name}` : `Enter exposure profile of ${instrument.name}`}
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm">
          As at
          <input className={inputClass} type="date" value={asAt} onChange={(e) => setAsAt(e.target.value)} />
          <FieldMessage error={save.error} field="as_at" />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Source note
          <input
            className={inputClass}
            placeholder="Where did these figures come from?"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <FieldMessage error={save.error} field="provenance_note" />
        </label>
      </div>
      {vocab.map((dimension) => {
        const texts = dimension.labels.map((l) => entered[`${dimension.key}.${l.key}`] ?? "");
        const state = judgeTotal(texts);
        return (
          <fieldset key={dimension.key} className="flex flex-col gap-2">
            <legend className="mb-1 text-sm font-medium">{dimension.name} weights (%)</legend>
            <div className="grid gap-2 sm:grid-cols-2">
              {dimension.labels.map((label) => {
                const key = `${dimension.key}.${label.key}`;
                return (
                  <label key={key} className="flex flex-col gap-1 text-sm">
                    {label.name}
                    <input
                      className={inputClass}
                      inputMode="decimal"
                      placeholder="0"
                      value={entered[key] ?? ""}
                      onChange={(e) => setEntered((prev) => ({ ...prev, [key]: e.target.value }))}
                    />
                    <FieldMessage error={save.error} field={`weights.${key}`} />
                  </label>
                );
              })}
            </div>
            <p
              role="status"
              aria-label={`${dimension.name} total`}
              className={
                state.state === "rejected"
                  ? "text-sm text-destructive"
                  : state.state === "short" || state.state === "over"
                    ? "text-sm text-warning"
                    : "text-sm text-muted-foreground"
              }
            >
              {totalMessage(state)}
            </p>
          </fieldset>
        );
      })}
      <FieldMessage error={save.error} field="weights" />
      <GeneralError error={save.error} />
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={save.isPending}>
          {initial ? "Save correction" : "Save profile"}
        </Button>
      </DialogFooter>
    </form>
  );
}

/** What the stored total means: a shortfall goes to Not classified, an excess is trimmed. */
function resolutionNotes(profile: ExposureProfile, vocab: ExposureDimension[]): string[] {
  return profile.resolutions.flatMap((r) => {
    if (r.outcome === "short")
      return [`Totals ${r.total_percent}%: the ${r.not_classified_percent}% shortfall shows as Not classified.`];
    if (r.outcome === "over")
      return [
        `Totals ${r.total_percent}%: ${r.trimmed
          .map((t) => `${t.percent}% is trimmed from ${labelName(vocab, r.dimension_key, t.label_key)}`)
          .join(", ")}, the largest weight.`,
      ];
    return [];
  });
}

function CurrentProfile({
  instrument,
  profile,
  vocab,
  today,
  onChange,
}: {
  instrument: Instrument;
  profile: ExposureProfile;
  vocab: ExposureDimension[];
  today: string;
  onChange: () => void;
}) {
  const [correcting, setCorrecting] = useState(false);
  const notes = resolutionNotes(profile, vocab);
  return (
    <Card aria-label={`Exposure profile as at ${profile.as_at}`} role="region">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle>As at {profile.as_at}</CardTitle>
        <Dialog open={correcting} onOpenChange={setCorrecting}>
          <DialogTrigger asChild>
            <Button variant="outline" aria-label={`Correct the profile as at ${profile.as_at}`}>
              Correct
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Correct the profile as at {profile.as_at}</DialogTitle>
              <DialogDescription>The original stays in the history as superseded.</DialogDescription>
            </DialogHeader>
            <ProfileForm
              instrument={instrument}
              vocab={vocab}
              today={today}
              initial={profile}
              onCancel={() => setCorrecting(false)}
              onDone={() => {
                setCorrecting(false);
                onChange();
              }}
            />
          </DialogContent>
        </Dialog>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <Badge variant={profile.source === "manual" ? "ok" : "info"}>{profile.source}</Badge>
          Entered {profile.retrieved_on}. {profile.provenance_note}
        </p>
        <Table aria-label={`Weights as at ${profile.as_at}`}>
          <TableHeader>
            <TableRow>
              <TableHead>Exposure label</TableHead>
              <TableHead className="text-right">Weight</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {profile.weights.map((w) => (
              <TableRow key={`${w.dimension_key}.${w.label_key}`}>
                <TableCell>{labelName(vocab, w.dimension_key, w.label_key)}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">{w.percent}%</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {notes.map((n) => (
          <p key={n} className="text-sm text-muted-foreground">
            {n}
          </p>
        ))}
      </CardContent>
    </Card>
  );
}

function summary(profile: ExposureProfile, vocab: ExposureDimension[]): string {
  return profile.weights
    .map((w) => `${labelName(vocab, w.dimension_key, w.label_key)} ${w.percent}%`)
    .join(", ");
}

/**
 * An Instrument's Exposure profile (ADR-0030): its asset-class weights entered by hand from a
 * published figure, with an as-at date and the source `manual`. A correction supersedes and the
 * earlier version stays in the history. The form shows the running total and how it will be judged;
 * the server decides (within one point is accepted, a shortfall shows as Not classified).
 */
export function ExposureProfilePanel({ instrument }: { instrument: Instrument }) {
  const queryClient = useQueryClient();
  const vocab = useQuery({ queryKey: ["exposure-vocabulary"], queryFn: fetchExposureVocabulary });
  const profiles = useQuery({
    queryKey: ["exposure-profiles", instrument.id],
    queryFn: () => fetchExposureProfiles(instrument.id),
  });
  const household = useQuery({ queryKey: ["household"], queryFn: fetchHousehold });
  const [adding, setAdding] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const today = todayIn(household.data?.time_zone ?? DEFAULT_TIME_ZONE);
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["exposure-profiles", instrument.id] });
    void queryClient.invalidateQueries({ queryKey: ["look-through"] });
  };
  const all = profiles.data ?? [];
  const current = all.filter((p) => p.status === "current");
  const earlier = all.filter((p) => p.status !== "current");
  const visible = showAll ? earlier : earlier.slice(0, SHOWN);
  const dimensions = vocab.data ?? [];

  return (
    <section aria-label={`Exposure profile of ${instrument.name}`} className="flex flex-col gap-3">
      <Dialog open={adding} onOpenChange={setAdding}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">Exposure profile</h2>
          <div className="flex flex-wrap gap-2">
            <Link
              to="/audit"
              search={{ entity_type: "exposure_profile" }}
              aria-label="Audit trail of Exposure profiles"
              className={buttonVariants({ variant: "outline" })}
            >
              Audit trail
            </Link>
            <DialogTrigger asChild>
              <Button variant="outline" disabled={!vocab.data}>
                <Plus size={16} />
                Enter exposure profile
              </Button>
            </DialogTrigger>
          </div>
        </div>
        <p className="text-sm text-muted-foreground">
          How {instrument.name} splits across asset classes, typed in from a published figure. A look-through on a
          date uses the latest profile on or before it. Weights within one point of 100% are accepted as printed.
        </p>
        {profiles.isPending && <p className="text-muted-foreground">Loading…</p>}
        {profiles.error && (
          <p className="text-destructive">Could not load the exposure profile: {profiles.error.message}</p>
        )}
        {profiles.data && current.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No exposure profile yet. A Holding of this Instrument shows as No profile in an Account&rsquo;s
            look-through.
          </p>
        )}
        {current.map((p) => (
          <CurrentProfile
            key={p.id}
            instrument={instrument}
            profile={p}
            vocab={dimensions}
            today={today}
            onChange={refresh}
          />
        ))}
        {earlier.length > 0 && (
          <div className="flex flex-col gap-2">
            <h3 className="text-sm font-medium">Earlier versions</h3>
            <Table aria-label={`Earlier exposure profiles of ${instrument.name}`}>
              <TableHeader>
                <TableRow>
                  <TableHead>As at</TableHead>
                  <TableHead>Weights</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {visible.map((p) => (
                  <TableRow key={p.id} data-status={p.status} className="align-top text-muted-foreground">
                    <TableCell className="whitespace-nowrap">
                      {p.as_at}
                      <Badge variant="superseded" className="mt-1 block w-fit">
                        Superseded {p.superseded_at?.slice(0, 10)}
                      </Badge>
                    </TableCell>
                    <TableCell className="break-words">
                      {summary(p, dimensions)}
                      <span className="block text-xs">
                        {p.source}, entered {p.retrieved_on}. {p.provenance_note}
                      </span>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {earlier.length > SHOWN && (
              <div>
                <Button variant="outline" onClick={() => setShowAll((v) => !v)}>
                  {showAll ? "Show fewer" : `Show all ${earlier.length} earlier versions`}
                </Button>
              </div>
            )}
          </div>
        )}
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Enter an exposure profile</DialogTitle>
            <DialogDescription>
              For {instrument.name}, as published. Leave a weight blank for none.
            </DialogDescription>
          </DialogHeader>
          <ProfileForm
            instrument={instrument}
            vocab={dimensions}
            today={today}
            onCancel={() => setAdding(false)}
            onDone={() => {
              setAdding(false);
              refresh();
            }}
          />
        </DialogContent>
      </Dialog>
    </section>
  );
}
