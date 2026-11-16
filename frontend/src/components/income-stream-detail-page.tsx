import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, MoreHorizontal } from "lucide-react";
import { useRef, useState } from "react";

import { GeneralError } from "@/components/form-fields";
import { IncomeStreamSheet } from "@/components/income-stream-form";
import { useRefreshIncomeStreams } from "@/components/income-streams-page";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Money } from "@/components/ui/money";
import { PageHeader } from "@/components/ui/page-header";
import { CATEGORY_LABEL } from "@/lib/projection";
import {
  deleteIncomeStream,
  endText,
  fetchIncomeStream,
  startText,
  type IncomeStream,
} from "@/lib/income-streams";

function BackLink() {
  return (
    <Link to="/income-streams" className={buttonVariants({ variant: "ghost" }) + " -ml-3 w-fit"}>
      <ArrowLeft size={16} aria-hidden />
      Income streams
    </Link>
  );
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

/** Delete income stream, always confirmed: it names the stream and says what stays. */
function DeleteDialog({
  stream,
  open,
  onOpenChange,
  onCloseAutoFocus,
}: {
  stream: IncomeStream;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCloseAutoFocus: (e: Event) => void;
}) {
  const navigate = useNavigate();
  const refresh = useRefreshIncomeStreams();
  const remove = useMutation({
    mutationFn: () => deleteIncomeStream(stream.id),
    onSuccess: () => {
      onOpenChange(false);
      refresh();
      void navigate({ to: "/income-streams" });
    },
  });
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) remove.reset();
        onOpenChange(next);
      }}
    >
      <AlertDialogContent onCloseAutoFocus={onCloseAutoFocus}>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete {stream.name}?</AlertDialogTitle>
          <AlertDialogDescription>
            Projections stop crediting this pay and its employer super. Saved projection runs keep the amounts they
            used. The deletion is recorded in the Audit trail.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <GeneralError error={remove.error} />
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            disabled={remove.isPending}
            onClick={(e) => {
              e.preventDefault(); // stay open until the server accepts, so errors show here
              remove.mutate();
            }}
          >
            Delete income stream
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function StreamDetail({ stream }: { stream: IncomeStream }) {
  const refresh = useRefreshIncomeStreams();
  const [deleting, setDeleting] = useState(false);
  const menuRef = useRef<HTMLButtonElement>(null);
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <BackLink />
        <PageHeader
          title={stream.name}
          description={
            <>
              {stream.type_label} · {stream.owners.map((o) => o.member_name).join(", ")}{" "}
              <Badge variant="info">{stream.tax_kind}</Badge>
            </>
          }
          action={
            <div className="flex items-center gap-2">
              <IncomeStreamSheet
                stream={stream}
                onSaved={refresh}
                trigger={<Button variant="outline">Edit income stream</Button>}
              />
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button ref={menuRef} variant="outline" aria-label="More actions" className="px-2">
                    <MoreHorizontal size={16} aria-hidden />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent>
                  <DropdownMenuItem destructive onSelect={() => setDeleting(true)}>
                    Delete income stream
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          }
        />
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Details</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-4 sm:grid-cols-2">
            <Detail label="Gross pay a year">
              <Money amount={stream.gross_annual} />
            </Detail>
            <Detail label="Take-home pay a year">
              <Money amount={stream.take_home_annual} />
            </Detail>
            <Detail label="Starts">{startText(stream)}</Detail>
            <Detail label="Ends">{endText(stream)}</Detail>
            <Detail label="Paid into">{CATEGORY_LABEL[stream.credit_category] ?? stream.credit_category}</Detail>
            {stream.notes && <Detail label="Notes">{stream.notes}</Detail>}
          </dl>
        </CardContent>
      </Card>
      <div>
        <Link
          to="/audit"
          search={{ entity_type: "income_stream", entity_id: stream.id }}
          className={`${buttonVariants({ variant: "link" })} h-auto max-w-full px-0 text-left whitespace-normal`}
        >
          View audit trail for {stream.name}
        </Link>
      </div>
      <DeleteDialog
        key={stream.id}
        stream={stream}
        open={deleting}
        onOpenChange={setDeleting}
        onCloseAutoFocus={(e) => {
          // Opened from a menu item, so hand focus back to the menu button.
          e.preventDefault();
          menuRef.current?.focus();
        }}
      />
    </div>
  );
}

/** `/income-streams/$streamId`: the stream's details, with Edit and a confirmed Delete. */
export function IncomeStreamDetailPage({ streamId }: { streamId: string }) {
  const id = Number(streamId);
  const valid = Number.isInteger(id) && id > 0;
  const stream = useQuery({
    queryKey: ["income-streams", "detail", id],
    queryFn: () => fetchIncomeStream(id),
    enabled: valid,
    retry: false,
  });
  if (stream.data) return <StreamDetail stream={stream.data} />;
  if (!valid || stream.error) {
    return (
      <div className="flex flex-col gap-3">
        <PageHeader title="Income stream not found" description="There is no income stream at this address." />
        <div>
          <BackLink />
        </div>
      </div>
    );
  }
  return <p className="text-muted-foreground">Loading…</p>;
}
