import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronDown, ChevronRight, MoreHorizontal } from "lucide-react";
import { useState } from "react";

import { GeneralError } from "@/components/form-fields";
import { WithdrawDialog } from "@/components/rule-figures-page";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  EFFORT_LEVELS,
  FROM_PARAM,
  FROM_WHATS_DUE,
  fetchWhatsDue,
  linkTarget,
  setEffortLevel,
  SNOOZE_CHOICES,
  snoozeDueItem,
  unsnoozeDueItem,
  WHATS_DUE_KEY,
  type DueAction,
  type DueItem,
  type EffortLevel,
  type SnoozeChoice,
  type SnoozedItem,
} from "@/lib/due";
import { fetchHousehold } from "@/lib/household";

/** The Effort level: how much Finance360 asks. Saved on the Household; the list refreshes. */
export function EffortLevelControl({ level }: { level: string }) {
  const queryClient = useQueryClient();
  const change = useMutation({
    mutationFn: async (next: EffortLevel) => setEffortLevel(await fetchHousehold(), next),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["household"] });
      await queryClient.invalidateQueries({ queryKey: WHATS_DUE_KEY });
    },
  });
  return (
    <fieldset className="flex flex-col gap-2" disabled={change.isPending}>
      <legend className="mb-2 text-sm font-medium">How hands-on do you want to be?</legend>
      <div className="grid gap-2 sm:grid-cols-3">
        {EFFORT_LEVELS.map((option) => (
          <label
            key={option.value}
            className="flex cursor-pointer flex-col gap-1 rounded-md border border-border p-3 text-sm has-[:checked]:border-primary has-[:checked]:bg-accent has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring"
          >
            <span className="flex items-center gap-2 font-medium">
              <input
                type="radio"
                name="effort-level"
                value={option.value}
                checked={level === option.value}
                onChange={() => change.mutate(option.value)}
              />
              {option.label}
            </span>
            <span className="text-muted-foreground">{option.description}</span>
          </label>
        ))}
      </div>
      <GeneralError error={change.error} />
      <p className="text-sm text-muted-foreground">
        You can change this at any time.{" "}
        <Link to="/guide" className="underline">
          Read more in the Guide
        </Link>
        .
      </p>
    </fieldset>
  );
}

function SnoozeMenu({ item }: { item: DueItem }) {
  const queryClient = useQueryClient();
  const snooze = useMutation({
    mutationFn: (choice: SnoozeChoice) => snoozeDueItem(item, choice),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: WHATS_DUE_KEY }),
  });
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" aria-label={`Snooze options for ${item.title}`} className="px-2">
          <MoreHorizontal size={16} />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        {SNOOZE_CHOICES.map((choice) => (
          <DropdownMenuItem key={choice.value} onSelect={() => snooze.mutate(choice.value)}>
            {choice.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** The buttons an item carries (Rule figures): "Keep mine" acknowledges it, "Use shipped" withdraws. */
function ItemActions({ item }: { item: DueItem }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState<DueAction | null>(null);
  const keep = useMutation({
    mutationFn: () => snoozeDueItem(item, "skip"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: WHATS_DUE_KEY }),
  });
  if (item.actions.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2 pt-1">
      {item.actions.map((action) =>
        action.kind === "keep_mine" ? (
          <Button key={action.kind} variant="outline" disabled={keep.isPending} onClick={() => keep.mutate()}>
            {action.label}
          </Button>
        ) : (
          <Button key={action.kind} variant="outline" onClick={() => setConfirming(action)}>
            {action.label}
          </Button>
        ),
      )}
      <GeneralError error={keep.error} />
      {confirming && (
        <WithdrawDialog
          captureId={confirming.capture_id}
          title={confirming.label + "?"}
          description="Your captured figure is withdrawn and the release's figure applies. The Audit trail keeps what you captured."
          open
          onOpenChange={(open) => !open && setConfirming(null)}
          onDone={() => {
            setConfirming(null);
            void queryClient.invalidateQueries();
          }}
        />
      )}
    </div>
  );
}

function ItemRow({ item }: { item: DueItem }) {
  const { to, search } = linkTarget(item.link);
  return (
    <li className="flex items-start justify-between gap-3 py-3">
      <div className="flex min-w-0 flex-col gap-1">
        <p className="text-sm font-medium">{item.title}</p>
        <p className="text-sm text-muted-foreground">{item.effect}</p>
        <Link to={to} search={{ ...search, [FROM_PARAM]: FROM_WHATS_DUE }} className="w-fit text-sm underline">
          Update it here
        </Link>
        <ItemActions item={item} />
      </div>
      <SnoozeMenu item={item} />
    </li>
  );
}

function ItemSection({ title, items }: { title: string; items: DueItem[] }) {
  if (items.length === 0) return null;
  const id = `due-${title.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <section aria-labelledby={id}>
      <Card>
        <CardHeader>
          <CardTitle id={id}>{title}</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="divide-y divide-border">
            {items.map((item) => (
              <ItemRow key={`${item.rule_key}|${item.subject_key}`} item={item} />
            ))}
          </ul>
        </CardContent>
      </Card>
    </section>
  );
}

function snoozeNote(snoozed: SnoozedItem): string {
  return snoozed.until_on ? `Snoozed until ${snoozed.until_on}` : "Skipped for this period";
}

function SnoozedSection({ snoozed }: { snoozed: SnoozedItem[] }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const unsnooze = useMutation({
    mutationFn: unsnoozeDueItem,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: WHATS_DUE_KEY }),
  });
  if (snoozed.length === 0) return null;
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <section>
      <Button variant="ghost" aria-expanded={open} onClick={() => setOpen(!open)} className="gap-1 px-2">
        <Chevron size={16} aria-hidden />
        Snoozed ({snoozed.length})
      </Button>
      {open && (
        <ul className="mt-2 divide-y divide-border rounded-xl border border-border px-4">
          {snoozed.map((s) => (
            <li key={s.snooze_id} className="flex items-start justify-between gap-3 py-3">
              <div className="flex min-w-0 flex-col gap-1">
                <p className="text-sm font-medium">{s.item.title}</p>
                <p className="text-sm text-muted-foreground">{snoozeNote(s)}</p>
              </div>
              <Button variant="outline" aria-label={`Unsnooze ${s.item.title}`} onClick={() => unsnooze.mutate(s.snooze_id)}>
                Unsnooze
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** The What's due section of Home (`/`): where the figures Finance360 uses could be refreshed (ADR-0020). */
export function WhatsDueSection() {
  // Coming back from an Account (the "Back to What's due" link) shows the list recalculated: it
  // refetches on every mount, and the cached list from before is not shown while that runs.
  const due = useQuery({ queryKey: WHATS_DUE_KEY, queryFn: fetchWhatsDue, refetchOnMount: "always" });
  const data = due.isFetchedAfterMount ? due.data : undefined;
  const important = data?.items.filter((i) => i.importance === "important") ?? [];
  const optional = data?.items.filter((i) => i.importance !== "important") ?? [];
  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h2 className="text-xl font-semibold">What's due</h2>
        <p className="text-sm text-muted-foreground">
          Where the figures Finance360 is using could be brought up to date, worked out from what you have entered.
        </p>
      </div>
      {data && <EffortLevelControl level={data.level} />}
      {due.isPending && <p className="text-sm text-muted-foreground">Loading...</p>}
      <GeneralError error={due.error} />
      {data && (
        <>
          {data.items.length === 0 && <p className="text-sm text-muted-foreground">Nothing to refresh right now.</p>}
          <ItemSection title="What matters most" items={important} />
          <ItemSection title="Also useful" items={optional} />
          {data.failed_rules.length > 0 && (
            <ul className="flex flex-col gap-1">
              {data.failed_rules.map((f) => (
                <li key={f.rule_key} className="text-sm text-muted-foreground">
                  Could not check {f.name}
                </li>
              ))}
            </ul>
          )}
          <SnoozedSection snoozed={data.snoozed} />
        </>
      )}
    </div>
  );
}
