import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { fetchStatus, isRunning, runFetchNow } from "@/lib/fetch";

/**
 * "Fetch now": fetches Prices and FX rates through the same service call as the daily job.
 * Reusable, e.g. after an Import that introduces new Instruments. It does nothing on an instance
 * with no provider configured (and says so), and it is disabled while a fetch is running.
 */
export function FetchNowButton({ variant = "default" }: { variant?: "default" | "outline" }) {
  const queryClient = useQueryClient();
  const status = useQuery({
    queryKey: ["fetch-status"],
    queryFn: fetchStatus,
    refetchInterval: (query) => (isRunning(query.state.data) ? 2000 : false),
  });
  const start = useMutation({
    mutationFn: runFetchNow,
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["fetch-status"] });
      void queryClient.invalidateQueries({ queryKey: ["fetch-runs"] });
    },
  });
  const configured = status.data?.datasets.some((d) => d.configured) ?? false;
  const running = start.isPending || isRunning(status.data);

  return (
    <div className="flex flex-col items-end gap-1">
      <Button variant={variant} onClick={() => start.mutate()} disabled={!configured || running}>
        <RefreshCw size={16} className={running ? "animate-spin" : undefined} aria-hidden />
        {running ? "Fetching…" : "Fetch now"}
      </Button>
      {start.error && (
        <p role="alert" className="text-sm text-destructive">
          {start.error.message}
        </p>
      )}
    </div>
  );
}
