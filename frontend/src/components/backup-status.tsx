import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { ago, fetchBackupStatus, formatBytes, runBackup } from "@/lib/backup";

/**
 * The footer's last-backup line (ADR-0011). A failure or a stale backup is shown in the error
 * colour with the reason: silently stale data is a bug. "Back up now" starts a manual run and the
 * line refreshes while it runs.
 */
export function BackupStatus() {
  const queryClient = useQueryClient();
  const { data, error } = useQuery({
    queryKey: ["backup-status"],
    queryFn: fetchBackupStatus,
    refetchInterval: (query) => (query.state.data?.latest?.status === "running" ? 3000 : false),
  });
  const start = useMutation({
    mutationFn: runBackup,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["backup-status"] }),
  });

  if (error) return <p className="text-destructive">Backup status unavailable: {error.message}</p>;
  if (!data) return null;

  const { latest, last_success: success } = data;
  const when = success ? ago(success.finished_at ?? success.started_at) : "";
  let line: ReactNode;
  let tone = "text-success";
  if (!data.configured) {
    tone = "text-destructive";
    line = "Backups are not set up on this instance.";
  } else if (latest?.status === "running") {
    tone = "text-muted-foreground";
    line = "Backup running…";
  } else if (data.stale || latest?.status === "failed") {
    tone = "text-destructive";
    const lastGood = success ? `Last successful backup ${when}` : "No successful backup yet";
    const failure = latest?.status === "failed" && latest.error ? `. Latest attempt failed: ${latest.error}` : ".";
    line = `${lastGood}${failure}`;
  } else if (success) {
    const size = success.size_bytes != null ? `, ${formatBytes(success.size_bytes)}` : "";
    const where = data.remote_configured ? (success.shipped ? ", copied off-site" : "") : ", local only";
    line = <span title={`${success.finished_at} UTC`}>{`Last backup: success, ${when}${size}${where}`}</span>;
  } else {
    tone = "text-muted-foreground";
    line = "No backup yet.";
  }

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <p className={tone} role="status">
        {line}
      </p>
      {data.configured && (
        <Button
          className="px-2 py-1 text-xs"
          onClick={() => start.mutate()}
          disabled={start.isPending || latest?.status === "running"}
        >
          Back up now
        </Button>
      )}
      {start.error && <p className="text-destructive">{start.error.message}</p>}
    </div>
  );
}
