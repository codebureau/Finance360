import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { mapInvestmentOption } from "@/lib/exposure";
import { fetchInstruments } from "@/lib/instruments";

type Account = { id: number; name: string; currency: string };

const CREATE = "create";

/**
 * Map one super Investment option to a fund Instrument (ADR-0030 section 4): pick an existing
 * Instrument, or create a fund from the option's name in the Account's currency. The Exposure
 * profile then attaches to that Instrument like any other, and a later Super statement with the
 * same name needs no new mapping.
 */
function MapOptionForm({
  account,
  option,
  onDone,
  onCancel,
}: {
  account: Account;
  option: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  const instruments = useQuery({ queryKey: ["instruments"], queryFn: fetchInstruments });
  const [choice, setChoice] = useState(CREATE);
  const save = useMutation({
    mutationFn: () =>
      mapInvestmentOption(
        account.id,
        choice === CREATE
          ? { option_name: option, create_instrument: true, instrument_id: null }
          : { option_name: option, create_instrument: false, instrument_id: Number(choice) },
      ),
    onSuccess: onDone,
  });
  return (
    <form
      aria-label={`Map ${option} to a fund`}
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <label className="flex flex-col gap-1 text-sm">
        Fund
        <select className={inputClass} value={choice} onChange={(e) => setChoice(e.target.value)}>
          <option value={CREATE}>Create a fund from “{option}”</option>
          {(instruments.data ?? []).map((i) => (
            <option key={i.id} value={i.id}>
              {i.name}
            </option>
          ))}
        </select>
        <FieldMessage error={save.error} field="instrument_id" />
        <FieldMessage error={save.error} field="option_name" />
      </label>
      <p className="text-sm text-muted-foreground">
        {choice === CREATE
          ? `A new fund is added in ${account.currency}, with no Exposure profile yet. Enter one from the fund's page.`
          : "Two Accounts whose option is the same product can share one fund, and so one Exposure profile."}
      </p>
      <GeneralError error={save.error} />
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={save.isPending}>
          Map to a fund
        </Button>
      </DialogFooter>
    </form>
  );
}

/** The Dialog around `MapOptionForm`; `option` is the open option's name, `null` when closed. */
export function MapOptionDialog({
  account,
  option,
  onClose,
}: {
  account: Account;
  option: string | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  return (
    <Dialog open={option !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Map {option} to a fund</DialogTitle>
          <DialogDescription>
            An Investment option takes its Exposure from the fund it is mapped to, for {account.name}.
          </DialogDescription>
        </DialogHeader>
        {option !== null && (
          <MapOptionForm
            account={account}
            option={option}
            onCancel={onClose}
            onDone={() => {
              onClose();
              void queryClient.invalidateQueries({ queryKey: ["look-through", account.id] });
              void queryClient.invalidateQueries({ queryKey: ["instruments"] });
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
