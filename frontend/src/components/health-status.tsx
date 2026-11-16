import { useQuery } from "@tanstack/react-query";

import { fetchHealth } from "@/lib/health";

/** A one-line backend status for the footer; it is diagnostic, so it does not earn a card. */
export function HealthStatus() {
  const { data, error, isPending } = useQuery({ queryKey: ["health"], queryFn: fetchHealth });

  if (isPending) return <p className="text-muted-foreground">Checking backend…</p>;
  if (error) return <p className="text-destructive">Unreachable: {error.message}</p>;
  return (
    <dl className="flex flex-wrap gap-x-4 gap-y-1">
      <div className="flex gap-1">
        <dt className="text-muted-foreground">Status</dt>
        <dd className="font-medium text-success">{data.status}</dd>
      </div>
      <div className="flex gap-1">
        <dt className="text-muted-foreground">Version</dt>
        <dd className="font-mono">{data.git_sha}</dd>
      </div>
      <div className="flex gap-1">
        <dt className="text-muted-foreground">Schema version</dt>
        <dd className="font-mono">{data.schema_version}</dd>
      </div>
    </dl>
  );
}
