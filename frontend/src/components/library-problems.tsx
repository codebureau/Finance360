import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { Badge } from "@/components/ui/badge";
import { checkLibrary, PROBLEM_TEXT } from "@/lib/library";

/** The Library's health check, shown only when a file is missing or changed. */
export function LibraryProblems() {
  const check = useQuery({ queryKey: ["library-check"], queryFn: () => checkLibrary() });
  const problems = check.data?.problems ?? [];
  if (problems.length === 0) return null;
  return (
    <div role="alert" className="flex flex-col gap-2 rounded-md border border-warning/40 bg-warning/10 p-3 text-sm">
      <p className="font-medium">
        {problems.length === 1 ? "A document needs attention" : `${problems.length} documents need attention`}
      </p>
      <ul className="flex flex-col gap-1">
        {problems.map((p) => (
          <li key={p.document_id} className="flex flex-wrap items-center gap-x-2">
            <Badge variant="flagged">{PROBLEM_TEXT[p.problem] ?? p.problem}</Badge>
            <span className="break-all">{p.file_name}</span>
            <Link
              to="/library/$documentId"
              params={{ documentId: String(p.document_id) }}
              className="text-primary underline underline-offset-4"
            >
              Open document {p.document_id}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
