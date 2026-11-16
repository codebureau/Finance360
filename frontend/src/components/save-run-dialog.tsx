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
import { saveRun, type RunHeader } from "@/lib/projection-runs";
import type { Scenario } from "@/lib/scenarios";

/** Save the Scenario's projection as it is now: an immutable run, with an optional label. */
export function SaveRunDialog({
  scenario,
  onSaved,
}: {
  scenario: Scenario;
  onSaved: (run: RunHeader) => void;
}) {
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState("");
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: () => saveRun(scenario.id, label),
    onSuccess: async (run) => {
      await queryClient.invalidateQueries({ queryKey: ["projection-runs"] });
      setOpen(false);
      setLabel("");
      onSaved(run);
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
        <Button>Save run</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Save run</DialogTitle>
          <DialogDescription>
            Keeps {scenario.name} exactly as projected now: the inputs, the result and the engine that computed
            it. A saved run never changes.
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
              placeholder="e.g. Before the pay rise"
              onChange={(e) => setLabel(e.target.value)}
            />
            <FieldMessage error={save.error} field="label" />
          </label>
          <GeneralError error={save.error} />
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={save.isPending}>
              Save run
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
