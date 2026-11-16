import { useQuery } from "@tanstack/react-query";

import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fetchCatalogue } from "@/lib/catalogue";

const CLASS_LABELS: Record<string, string> = { asset: "Asset", liability: "Liability" };

/** "simple_balance" -> "simple balance" */
const behaviourLabel = (key: string) => key.replaceAll("_", " ");

/** Read-only: Class -> Category -> Account types. Editing is out of scope for M1. */
export function CategoriesPage() {
  const { data, error, isPending } = useQuery({ queryKey: ["catalogue"], queryFn: fetchCatalogue });

  return (
    <div>
      <PageHeader title="Categories" description="Every Account type, grouped by Class and Category." />
      {isPending && <p className="text-muted-foreground">Loading…</p>}
      {error && <p className="text-destructive">Could not load the Categories: {error.message}</p>}
      <div className="grid gap-6 lg:grid-cols-2 lg:items-start">
        {data?.classes.map((cls) => {
          const label = CLASS_LABELS[cls.class] ?? cls.class;
          return (
            <section key={cls.class} aria-label={label}>
              <h3 className="mb-2 text-lg font-semibold">{label}</h3>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Category</TableHead>
                    <TableHead>Account types</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {cls.categories.map((cat) => (
                    <TableRow key={cat.key}>
                      <TableCell className="align-top font-medium">{cat.name}</TableCell>
                      <TableCell>
                        <ul className="flex flex-col gap-1 text-muted-foreground">
                          {cat.account_types.map((t) => (
                            <li key={t.key}>
                              {t.name} <span className="font-mono">({behaviourLabel(t.behaviour_key)})</span>
                            </li>
                          ))}
                        </ul>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </section>
          );
        })}
      </div>
    </div>
  );
}
