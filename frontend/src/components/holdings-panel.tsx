import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";

import {
  FieldMessage,
  GeneralError,
  inputClass,
} from "@/components/form-fields";
import { Button } from "@/components/ui/button";
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
import { Money } from "@/components/ui/money";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fetchInstruments } from "@/lib/instruments";
import { addOpeningPosition, fetchHoldings } from "@/lib/transactions";

type Account = {
  id: number;
  name: string;
  currency: string;
  closed_on?: string | null;
};
type Kind = "holding" | "cash";

/**
 * Record an Opening position: units of an Instrument or cash the Account already had before its
 * first imported Transaction. Dated, manual and audited; it is never guessed from a file.
 */
function OpeningPositionForm({
  account,
  onDone,
  onCancel,
}: {
  account: Account;
  onDone: () => void;
  onCancel: () => void;
}) {
  const instruments = useQuery({
    queryKey: ["instruments"],
    queryFn: fetchInstruments,
  });
  const [kind, setKind] = useState<Kind>("holding");
  const [occurredOn, setOccurredOn] = useState("");
  const [instrumentId, setInstrumentId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [amount, setAmount] = useState("");
  const save = useMutation({
    mutationFn: () =>
      addOpeningPosition(account.id, {
        kind,
        occurred_on: occurredOn || null,
        instrument_id:
          kind === "holding" && instrumentId ? Number(instrumentId) : null,
        quantity: kind === "holding" ? quantity : "",
        amount: kind === "cash" ? amount : "",
      }),
    onSuccess: onDone,
  });

  return (
    <form
      aria-label={`Record Opening position for ${account.name}`}
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <label className="flex flex-col gap-1 text-sm">
        What it is
        <select
          className={inputClass}
          value={kind}
          onChange={(e) => setKind(e.target.value as Kind)}
        >
          <option value="holding">Units of an Instrument</option>
          <option value="cash">Cash</option>
        </select>
        <FieldMessage error={save.error} field="kind" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Date
        <input
          className={inputClass}
          type="date"
          value={occurredOn}
          onChange={(e) => setOccurredOn(e.target.value)}
        />
        <FieldMessage error={save.error} field="occurred_on" />
      </label>
      {kind === "holding" ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-sm">
            Instrument
            <select
              className={inputClass}
              value={instrumentId}
              onChange={(e) => setInstrumentId(e.target.value)}
            >
              <option value="">Choose an Instrument</option>
              {(instruments.data ?? []).map((i) => (
                <option key={i.id} value={i.id}>
                  {i.name}
                </option>
              ))}
            </select>
            <FieldMessage error={save.error} field="instrument_id" />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Units
            <input
              className={inputClass}
              inputMode="decimal"
              placeholder="0"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
            />
            <FieldMessage error={save.error} field="quantity" />
          </label>
        </div>
      ) : (
        <label className="flex flex-col gap-1 text-sm">
          Amount ({account.currency})
          <input
            className={inputClass}
            inputMode="decimal"
            placeholder="0.00"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
          <FieldMessage error={save.error} field="amount" />
        </label>
      )}
      <GeneralError error={save.error} />
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={save.isPending}>
          Record Opening position
        </Button>
      </DialogFooter>
    </form>
  );
}

/**
 * An Account's Holdings (units per Instrument) and cash balance as at a date, derived from its
 * current Transactions. The as-at date is a search param, so a view is linkable. A balance that
 * went negative is explained, never shown as a value (ADR-0013 section 7).
 */
export function HoldingsPanel({
  account,
  asAt,
  onAsAt,
}: {
  account: Account;
  asAt: string | undefined;
  onAsAt: (asAt: string | undefined) => void;
}) {
  const queryClient = useQueryClient();
  const [recording, setRecording] = useState(false);
  const holdings = useQuery({
    queryKey: ["holdings", account.id, asAt ?? "latest"],
    queryFn: () => fetchHoldings(account.id, asAt),
    placeholderData: keepPreviousData,
  });
  const data = holdings.data;
  const empty = data && data.holdings.length === 0 && data.cash.length === 0;

  return (
    <Card aria-label="Holdings" role="region">
      <Dialog open={recording} onOpenChange={setRecording}>
        <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
          <CardTitle>Holdings</CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-2 text-sm">
              As at
              <input
                className={inputClass}
                type="date"
                value={asAt ?? ""}
                onChange={(e) => onAsAt(e.target.value || undefined)}
              />
            </label>
            {!account.closed_on && (
              <DialogTrigger asChild>
                <Button variant="outline">
                  <Plus size={16} />
                  Record Opening position
                </Button>
              </DialogTrigger>
            )}
          </div>
        </CardHeader>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              Record an Opening position for {account.name}
            </DialogTitle>
            <DialogDescription>
              What the Account already held before its first imported
              Transaction. Date it before the first imported row.
            </DialogDescription>
          </DialogHeader>
          <OpeningPositionForm
            account={account}
            onCancel={() => setRecording(false)}
            onDone={() => {
              setRecording(false);
              void queryClient.invalidateQueries({
                queryKey: ["holdings", account.id],
              });
              void queryClient.invalidateQueries({
                queryKey: ["transactions", account.id],
              });
              void queryClient.invalidateQueries({ queryKey: ["imports"] });
              // the Account's derived value and look-through are built from these Holdings
              void queryClient.invalidateQueries({
                queryKey: ["derived-value", account.id],
              });
              void queryClient.invalidateQueries({
                queryKey: ["look-through", account.id],
              });
            }}
          />
        </DialogContent>
        <CardContent className="flex flex-col gap-3">
          {holdings.error && (
            <p className="text-destructive">
              Could not load the Holdings: {holdings.error.message}
            </p>
          )}
          {data?.invalid.map((n) => (
            <p
              key={`${n.instrument_id ?? n.currency}`}
              role="alert"
              className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm break-words"
            >
              {n.message}
            </p>
          ))}
          {empty && data.invalid.length === 0 && (
            <p className="text-sm text-muted-foreground">
              Nothing held{asAt ? ` on ${asAt}` : ""}. Holdings come from
              committed Imports and Opening positions.
            </p>
          )}
          {data && (data.holdings.length > 0 || data.cash.length > 0) && (
            <Table aria-label="Holdings table">
              <TableHeader>
                <TableRow>
                  <TableHead>Holding</TableHead>
                  <TableHead className="text-right">Units</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.holdings.map((h) => (
                  <TableRow key={h.instrument_id}>
                    <TableCell className="break-words">
                      {h.instrument_name}
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {h.units}
                    </TableCell>
                  </TableRow>
                ))}
                {data.cash.map((m) => (
                  <TableRow key={m.currency}>
                    <TableCell>Cash ({m.currency})</TableCell>
                    <TableCell className="text-right">
                      <Money amount={m.amount} currency={m.currency} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Dialog>
    </Card>
  );
}
