import { useMutation, useQueryClient } from "@tanstack/react-query";
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
  DialogTrigger,
} from "@/components/ui/dialog";
import { saveEstimate, type EstimateRequest, type TaxEstimateHeader } from "@/lib/tax-estimates";

/**
 * Save the estimate as it is calculated now: an immutable Tax estimate that holds its own inputs,
 * result and the engine that computed it. It is recalculated on the server from the same request.
 */
export function SaveEstimateDialog({
  request,
  onSaved,
  variant = "outline",
}: {
  request: EstimateRequest;
  onSaved: (saved: TaxEstimateHeader) => void;
  variant?: "default" | "outline";
}) {
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState("");
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: () => saveEstimate(request, label),
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: ["tax-estimates"] });
      setOpen(false);
      setLabel("");
      onSaved(saved);
    },
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) save.reset();
      }}
    >
      <DialogTrigger asChild>
        <Button variant={variant}>Save estimate</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Save estimate</DialogTitle>
          <DialogDescription>
            Keeps this estimate exactly as calculated now: the totals it used, the Rule figures, the result and the
            engine that computed it. A saved estimate never changes, even if your Tax records do.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            Label (optional)
            <input
              className={inputClass}
              value={label}
              maxLength={100}
              placeholder="e.g. Before the laptop"
              onChange={(e) => setLabel(e.target.value)}
            />
            <FieldMessage error={save.error} field="label" />
          </label>
          <FieldMessage error={save.error} field="financial_year" />
          <GeneralError error={save.error} />
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={save.isPending}>
              Save estimate
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
