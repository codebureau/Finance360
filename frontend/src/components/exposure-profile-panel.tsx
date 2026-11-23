import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Plus, X } from "lucide-react";
import { useId, useMemo, useState } from "react";

import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
import { FiguresDisclosure } from "@/components/figures-disclosure";
import { FORM_OPTIONS, ShareBody } from "@/components/share-chart";
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
import { SegmentedControl } from "@/components/ui/segmented-control";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { SHARE_FORMS, shareChartLabel, type ShareForm } from "@/lib/charts/share";
import { useDisplayPreference } from "@/lib/display-preferences";
import {
  fetchExposureProfiles,
  fetchExposureVocabulary,
  judgeTotal,
  percentToPpm,
  ppmToPercent,
  recordExposureProfile,
  sumPpm,
  WEIGHT_TOLERANCE_PPM,
  type ExposureDimension,
  type ExposureProfile,
} from "@/lib/exposure";
import { profileHeadline, profileRows, referenceRows, totalBarGeometry } from "@/lib/exposure-chart";
import { ApiError, fetchHousehold, todayIn } from "@/lib/household";
import { fetchInstruments, type Instrument } from "@/lib/instruments";

const DEFAULT_TIME_ZONE = "Australia/Melbourne";
/** Earlier versions shown before "Show all". */
const SHOWN = 5;

const labelName = (vocab: ExposureDimension[], dimension: string, label: string) =>
  vocab.find((d) => d.key === dimension)?.labels.find((l) => l.key === label)?.name ?? label;

const dimensionName = (vocab: ExposureDimension[], dimension: string) =>
  vocab.find((d) => d.key === dimension)?.name ?? dimension;

/** A profile's weights by Dimension, in the order it gives them. Label names repeat across
 * Dimensions ("Other"), so weights are always shown under their Dimension. */
function byDimension(profile: ExposureProfile) {
  const groups = new Map<string, ExposureProfile["weights"]>();
  for (const w of profile.weights) groups.set(w.dimension_key, [...(groups.get(w.dimension_key) ?? []), w]);
  return [...groups];
}

/** How the running total of one Dimension will be judged (the server decides; this previews). */
function totalMessage(state: ReturnType<typeof judgeTotal>, referencesPpm: number): string {
  const total = ppmToPercent(state.totalPpm);
  const gap = ppmToPercent(Math.abs(state.differencePpm));
  const withFunds = referencesPpm > 0 ? ` (with ${ppmToPercent(referencesPpm)}% in underlying funds)` : "";
  switch (state.state) {
    case "empty":
      return referencesPpm > 0
        ? `Nothing entered: this Dimension is left out, and what the underlying funds do not cover (${ppmToPercent(Math.max(0, 1_000_000 - referencesPpm))}%) shows as Not classified.`
        : "Nothing entered: this Dimension is left out.";
    case "exact":
      return `Total ${total}%${withFunds}. Adds to 100%.`;
    case "short":
      return `Total ${total}%${withFunds}: ${gap} points short of 100%. The shortfall shows as Not classified.`;
    case "over":
      return `Total ${total}%${withFunds}: ${gap} points over 100%. The excess is trimmed from the largest weight.`;
    default:
      return `Total ${total}%${withFunds}: ${gap} points ${state.differencePpm < 0 ? "short of" : "over"} 100%. More than ${ppmToPercent(WEIGHT_TOLERANCE_PPM)} point either way is rejected.`;
  }
}

/** The running total filling towards 100%, with the accepted band (within one point) marked. The
 * message beside it says the same in words; the bar is only a visual cue. */
function TotalBar({ state }: { state: ReturnType<typeof judgeTotal> }) {
  const g = totalBarGeometry(state.totalPpm);
  const tone =
    state.state === "exact"
      ? "bg-success"
      : state.state === "rejected"
        ? "bg-destructive"
        : "bg-warning";
  return (
    <div
      aria-hidden="true"
      data-testid="total-bar"
      data-state={state.state}
      className="relative h-2 w-full overflow-hidden rounded-full bg-muted"
    >
      {state.state !== "empty" && <div className={`h-full ${tone}`} style={{ width: `${g.fill}%` }} />}
      <div
        className="absolute inset-y-0 border-x border-foreground/60"
        style={{ left: `${g.bandStart}%`, width: `${g.bandEnd - g.bandStart}%` }}
      />
    </div>
  );
}

/**
 * One Dimension's weights. Each is a disclosure so the form stays short at phone width (four
 * Dimensions are about forty fields): the first, and any that already has figures, starts open. A
 * Dimension may be left blank, which leaves it out of the profile.
 */
function DimensionFields({
  dimension,
  entered,
  error,
  initiallyOpen,
  referencesPpm,
  onChange,
}: {
  dimension: ExposureDimension;
  entered: Record<string, string>;
  error: unknown;
  initiallyOpen: boolean;
  /** What the underlying funds already cover: it counts towards this Dimension's total. */
  referencesPpm: number;
  onChange: (key: string, value: string) => void;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  // a rejected weight is never hidden in a closed Dimension
  const failed =
    error instanceof ApiError && Object.keys(error.fields).some((f) => f.startsWith(`weights.${dimension.key}.`));
  const state = judgeTotal(
    dimension.labels.map((l) => entered[`${dimension.key}.${l.key}`] ?? ""),
    referencesPpm,
  );
  return (
    <details
      open={open || failed}
      onToggle={(e) => setOpen(e.currentTarget.open)}
      className="rounded-md border border-border px-3 py-2"
    >
      <summary className="cursor-pointer text-sm font-medium">
        {dimension.name} weights (%)
        <span className="ml-2 font-normal text-muted-foreground">
          {state.state === "empty" ? "left out" : `total ${ppmToPercent(state.totalPpm)}%`}
        </span>
      </summary>
      <fieldset className="mt-2 flex flex-col gap-2">
        <legend className="sr-only">{dimension.name} weights (%)</legend>
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
                  onChange={(e) => onChange(key, e.target.value)}
                />
                <FieldMessage error={error} field={`weights.${key}`} />
              </label>
            );
          })}
        </div>
        <TotalBar state={state} />
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
          {totalMessage(state, referencesPpm)}
        </p>
      </fieldset>
    </details>
  );
}

type ReferenceEntry = { instrumentId: string; percent: string };

/**
 * A fund-of-funds' underlying funds: each a share of the whole profile, picked from the Instruments
 * and typed in from the fund's documents. They count towards every Dimension's total, so a profile
 * of 60% in one fund has its own weights add up to 40%. A loop of funds is rejected by the server
 * with a message naming it.
 */
function ReferenceFields({
  instrument,
  entered,
  error,
  onChange,
}: {
  instrument: Instrument;
  entered: ReferenceEntry[];
  error: unknown;
  onChange: (next: ReferenceEntry[]) => void;
}) {
  const instruments = useQuery({ queryKey: ["instruments"], queryFn: fetchInstruments });
  const options = (instruments.data ?? []).filter((i) => i.id !== instrument.id);
  const total = sumPpm(entered.map((e) => e.percent));
  return (
    <fieldset className="flex flex-col gap-2 rounded-md border border-border px-3 py-2">
      <legend className="px-1 text-sm font-medium">Underlying funds</legend>
      <p className="text-sm text-muted-foreground">
        For a fund of funds: the funds it holds, as a share of {instrument.name}. They count towards every Dimension
        below, which then only needs the rest. Leave empty for an ordinary fund.
      </p>
      {entered.map((entry, index) => (
        <div key={index} className="flex flex-wrap items-end gap-2">
          <label className="flex min-w-0 flex-1 flex-col gap-1 text-sm">
            Underlying fund {index + 1}
            <select
              className={inputClass}
              value={entry.instrumentId}
              onChange={(e) => onChange(entered.map((x, i) => (i === index ? { ...x, instrumentId: e.target.value } : x)))}
            >
              <option value="">Choose an Instrument</option>
              {options.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.name}
                </option>
              ))}
            </select>
            {entry.instrumentId !== "" && <FieldMessage error={error} field={`references.${entry.instrumentId}`} />}
          </label>
          <label className="flex w-28 flex-col gap-1 text-sm">
            Weight (%)
            <input
              className={inputClass}
              inputMode="decimal"
              aria-label={`Underlying fund ${index + 1} weight (%)`}
              value={entry.percent}
              onChange={(e) => onChange(entered.map((x, i) => (i === index ? { ...x, percent: e.target.value } : x)))}
            />
          </label>
          <Button
            type="button"
            variant="ghost"
            aria-label={`Remove underlying fund ${index + 1}`}
            onClick={() => onChange(entered.filter((_, i) => i !== index))}
          >
            <X size={16} />
          </Button>
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="outline" onClick={() => onChange([...entered, { instrumentId: "", percent: "" }])}>
          <Plus size={16} />
          Add underlying fund
        </Button>
        {entered.length > 0 && (
          <p role="status" aria-label="Underlying funds total" className="text-sm text-muted-foreground">
            Underlying funds total {ppmToPercent(total)}%.
          </p>
        )}
      </div>
      <FieldMessage error={error} field="references" />
    </fieldset>
  );
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
  const [references, setReferences] = useState<ReferenceEntry[]>(() =>
    (initial?.references ?? []).map((r) => ({ instrumentId: String(r.instrument_id), percent: r.percent })),
  );
  const referencesPpm = sumPpm(references.map((r) => r.percent));
  const save = useMutation({
    mutationFn: () =>
      recordExposureProfile(instrument.id, {
        as_at: asAt,
        provenance_note: note,
        references: references
          .filter((r) => r.instrumentId !== "" && r.percent.trim() !== "")
          .map((r) => ({ instrument_id: Number(r.instrumentId), percent: r.percent.trim() })),
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
      <ReferenceFields instrument={instrument} entered={references} error={save.error} onChange={setReferences} />
      {vocab.map((dimension, index) => (
        <DimensionFields
          key={dimension.key}
          dimension={dimension}
          entered={entered}
          error={save.error}
          referencesPpm={referencesPpm}
          initiallyOpen={index === 0 || dimension.labels.some((l) => (entered[`${dimension.key}.${l.key}`] ?? "") !== "")}
          onChange={(key, value) => setEntered((prev) => ({ ...prev, [key]: value }))}
        />
      ))}
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
    const name = dimensionName(vocab, r.dimension_key);
    if (r.outcome === "short")
      return [`${name} totals ${r.total_percent}%: the ${r.not_classified_percent}% shortfall shows as Not classified.`];
    if (r.outcome === "over")
      return [
        `${name} totals ${r.total_percent}%: ${[
          ...r.trimmed.map((t) => `${t.percent}% is trimmed from ${labelName(vocab, r.dimension_key, t.label_key)}`),
          ...r.trimmed_references.map((t) => `${t.percent}% is trimmed from ${t.instrument_name}`),
        ].join(", ")}, the largest weight.`,
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
  const groupId = useId();
  // One switch serves the card's several small charts (ADR-0031): `exposure-profile`.
  const pref = useDisplayPreference<ShareForm>("exposure-profile", "share", SHARE_FORMS);
  const notes = resolutionNotes(profile, vocab);
  const rows = useMemo(() => {
    const through = profile.references.reduce((sum, r) => sum + (percentToPpm(r.percent) ?? 0), 0);
    return new Map(vocab.map((d) => [d.key, profileRows(profile.weights, d, vocab, through)]));
  }, [profile, vocab]);
  const rowsOf = (d: ExposureDimension) => rows.get(d.key) ?? [];
  const fundRows = useMemo(() => referenceRows(profile), [profile]);
  const fundOfFunds = profile.references.length > 0;
  const headline = profileHeadline(profile, vocab);
  const labelOf = (what: string) => `${shareChartLabel(pref.form, headline)} ${what}`;
  return (
    <Card aria-label={`Exposure profile as at ${profile.as_at}`} role="region">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle>As at {profile.as_at}</CardTitle>
        <div className="flex flex-wrap items-center gap-2">
          <SegmentedControl
            label="Chart type"
            name={`exposure-profile-form-${groupId}`}
            value={pref.form}
            options={FORM_OPTIONS}
            onChange={pref.choose}
          />
          {pref.canUseAsDefault && (
            <Button variant="outline" onClick={pref.makeDefault}>
              Use as my default
            </Button>
          )}
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
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-base font-medium break-words">{headline}</p>
        <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <Badge variant={profile.source === "manual" ? "ok" : "info"}>{profile.source}</Badge>
          Entered {profile.retrieved_on}. {profile.provenance_note}
        </p>
        {!pref.ready ? (
          <p className="text-sm text-muted-foreground">Loading chart</p>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            {fundOfFunds && (
              <section aria-label="Underlying funds split" className="flex min-w-0 flex-col gap-1">
                <h3 className="text-sm font-medium">Underlying funds</h3>
                <ShareBody
                  form={pref.form}
                  rows={fundRows}
                  stacked
                  label={labelOf(`Share of ${instrument.name} held in each underlying fund.`)}
                  legendLabel="Underlying funds: legend"
                />
              </section>
            )}
            {vocab.map((d) => {
              const dimensionRows = rowsOf(d);
              return (
                <section key={d.key} aria-label={`${d.name} split`} className="flex min-w-0 flex-col gap-1">
                  <h3 className="text-sm font-medium">{d.name}</h3>
                  {dimensionRows.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      {fundOfFunds ? "Only through its underlying funds" : "Not published"}
                    </p>
                  ) : (
                    <>
                      {fundOfFunds && (
                        <p className="text-xs text-muted-foreground">Its own holdings, beside the share held through its underlying funds.</p>
                      )}
                      <ShareBody
                        form={pref.form}
                        rows={dimensionRows}
                        stacked
                        label={labelOf(`${instrument.name} by ${d.name.toLowerCase()}.`)}
                        legendLabel={`${d.name}: legend`}
                      />
                    </>
                  )}
                </section>
              );
            })}
          </div>
        )}
        <FiguresDisclosure>
          <Table aria-label={`Weights as at ${profile.as_at}`}>
            <TableHeader>
              <TableRow>
                <TableHead>Exposure label</TableHead>
                <TableHead className="text-right">Weight</TableHead>
              </TableRow>
            </TableHeader>
            {fundOfFunds && (
              <TableBody aria-label="Underlying funds" className="border-t border-border">
                <TableRow>
                  <TableHead scope="rowgroup" colSpan={2} className="bg-muted/30 text-left">
                    Underlying funds
                  </TableHead>
                </TableRow>
                {profile.references.map((r) => (
                  <TableRow key={r.instrument_id}>
                    <TableCell>
                      <Link
                        to="/instruments/$instrumentId"
                        params={{ instrumentId: String(r.instrument_id) }}
                        className="text-primary underline underline-offset-2"
                      >
                        {r.instrument_name}
                      </Link>
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{r.percent}%</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            )}
            {byDimension(profile).map(([key, weights]) => (
              <TableBody key={key} aria-label={dimensionName(vocab, key)} className="border-t border-border">
                <TableRow>
                  <TableHead scope="rowgroup" colSpan={2} className="bg-muted/30 text-left">
                    {dimensionName(vocab, key)}
                  </TableHead>
                </TableRow>
                {weights.map((w) => (
                  <TableRow key={w.label_key}>
                    <TableCell>{labelName(vocab, key, w.label_key)}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{w.percent}%</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            ))}
          </Table>
        </FiguresDisclosure>
        {fundOfFunds && (
          <p className="text-sm text-muted-foreground">
            A fund of funds is resolved through the profile of each underlying fund on the look-through date, so a
            corrected underlying profile flows through. An underlying fund with no profile shows as No profile.
          </p>
        )}
        {notes.map((n) => (
          <p key={n} className="text-sm text-muted-foreground">
            {n}
          </p>
        ))}
      </CardContent>
    </Card>
  );
}

/** The most weights a header summary lists before "and more". */
const SUMMARY_SHOWN = 3;

/**
 * An Instrument's **Exposure currency** from its current profile, shown beside its Holding
 * currency in the header: what the underlying assets are exposed to, not what the Instrument is
 * priced in. Says so plainly when there is no profile or the profile leaves the Dimension out.
 */
export function ExposureCurrencySummary({ instrument }: { instrument: Instrument }) {
  const vocab = useQuery({ queryKey: ["exposure-vocabulary"], queryFn: fetchExposureVocabulary });
  const profiles = useQuery({
    queryKey: ["exposure-profiles", instrument.id],
    queryFn: () => fetchExposureProfiles(instrument.id),
  });
  if (!profiles.data || !vocab.data) return null;
  const current = profiles.data
    .filter((p) => p.status === "current")
    .sort((a, b) => b.as_at.localeCompare(a.as_at))[0];
  const weights = (current?.weights ?? [])
    .filter((w) => w.dimension_key === "exposure_currency")
    .sort((a, b) => (percentToPpm(b.percent) ?? 0) - (percentToPpm(a.percent) ?? 0));
  const text = !current
    ? "no profile yet"
    : weights.length === 0
      ? "not in its profile"
      : weights
          .slice(0, SUMMARY_SHOWN)
          .map((w) => `${labelName(vocab.data, w.dimension_key, w.label_key)} ${w.percent}%`)
          .join(", ") + (weights.length > SUMMARY_SHOWN ? ` and ${weights.length - SUMMARY_SHOWN} more` : "");
  // A fund-of-funds' own weights cover only what it holds directly; say so rather than imply a whole.
  const through = (current?.references ?? []).reduce((sum, r) => sum + (percentToPpm(r.percent) ?? 0), 0);
  const suffix =
    current && through > 0
      ? weights.length === 0
        ? ` (${ppmToPercent(through)}% through underlying funds)`
        : ` directly, ${ppmToPercent(through)}% through underlying funds`
      : "";
  return (
    <span className="text-muted-foreground">
      Exposure currency: <span className="text-foreground">{text + suffix}</span>
    </span>
  );
}

function summary(profile: ExposureProfile, vocab: ExposureDimension[]): string {
  return byDimension(profile)
    .map(
      ([key, weights]) =>
        `${dimensionName(vocab, key)}: ${weights.map((w) => `${labelName(vocab, key, w.label_key)} ${w.percent}%`).join(", ")}`,
    )
    .join("; ");
}

/**
 * An Instrument's Exposure profile (ADR-0030): its weights in each Dimension (asset class, region,
 * sector, exposure currency) entered by hand from a published figure, any Dimension optional, with an as-at date and the source `manual`. A correction supersedes and the
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
          How {instrument.name} splits across asset class, region, sector and exposure currency, typed in from a
          published figure. Leave a Dimension blank if the figure is not published. A look-through on a
          date uses the latest profile on or before it. Weights within one point of 100% are accepted as printed.
          A fund of funds lists the funds it holds under Underlying funds instead of repeating their weights.
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
              For {instrument.name}, as published. Leave a weight blank for none, or a whole Dimension blank to leave it out.
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
