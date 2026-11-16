import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MoreHorizontal, Plus } from "lucide-react";
import { useRef, useState } from "react";

import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { PageHeader } from "@/components/ui/page-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  addMember,
  deleteMember,
  fetchHousehold,
  renameHousehold,
  setTimeZone,
  updateMember,
  type Member,
  type MemberInput,
} from "@/lib/household";

function MemberForm({
  initial,
  submitLabel,
  onSubmit,
  onDone,
  onCancel,
}: {
  initial: MemberInput;
  submitLabel: string;
  onSubmit: (input: MemberInput) => Promise<unknown>;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial.name);
  const [dob, setDob] = useState(initial.date_of_birth);
  const mutation = useMutation({ mutationFn: onSubmit, onSuccess: onDone });

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        mutation.mutate({ name, date_of_birth: dob });
      }}
    >
      <label className="flex flex-col gap-1 text-sm">
        Name
        <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
        <FieldMessage error={mutation.error} field="name" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Date of birth
        <input className={inputClass} type="date" value={dob} onChange={(e) => setDob(e.target.value)} />
        <FieldMessage error={mutation.error} field="date_of_birth" />
      </label>
      <GeneralError error={mutation.error} />
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={mutation.isPending}>
          {submitLabel}
        </Button>
      </DialogFooter>
    </form>
  );
}

function MemberRow({ member, onChange }: { member: Member; onChange: () => void }) {
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const menuRef = useRef<HTMLButtonElement>(null);
  const remove = useMutation({ mutationFn: () => deleteMember(member.id), onSuccess: onChange });
  // The dialogs open from a menu item, so hand focus back to the row's menu button on close.
  const returnFocus = (e: Event) => {
    e.preventDefault();
    menuRef.current?.focus();
  };

  return (
    <TableRow>
      <TableCell>
        <span className="font-medium">{member.name}</span>
        <GeneralError error={remove.error} />
      </TableCell>
      <TableCell className="whitespace-nowrap text-muted-foreground">Born {member.date_of_birth}</TableCell>
      <TableCell className="w-10 text-right">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button ref={menuRef} variant="ghost" aria-label={`Actions for ${member.name}`} className="px-2">
              <MoreHorizontal size={16} />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuItem onSelect={() => setEditing(true)}>Edit Member</DropdownMenuItem>
            <DropdownMenuItem destructive onSelect={() => setDeleting(true)}>
              Delete Member
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <Dialog open={editing} onOpenChange={setEditing}>
          <DialogContent onCloseAutoFocus={returnFocus}>
            <DialogHeader>
              <DialogTitle>Edit {member.name}</DialogTitle>
              <DialogDescription>Change this Member's name or date of birth.</DialogDescription>
            </DialogHeader>
            <MemberForm
              initial={{ name: member.name, date_of_birth: member.date_of_birth }}
              submitLabel="Save"
              onSubmit={(input) => updateMember(member.id, input)}
              onCancel={() => setEditing(false)}
              onDone={() => {
                setEditing(false);
                onChange();
              }}
            />
          </DialogContent>
        </Dialog>
        <AlertDialog open={deleting} onOpenChange={setDeleting}>
          <AlertDialogContent onCloseAutoFocus={returnFocus}>
            <AlertDialogHeader>
              <AlertDialogTitle>Delete {member.name}?</AlertDialogTitle>
              <AlertDialogDescription>
                {member.name} is removed from the Household. A Member who owns an Account cannot be deleted.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction onClick={() => remove.mutate()}>Delete Member</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </TableCell>
    </TableRow>
  );
}

/** One short text setting (Household name or time zone) edited in a Dialog. */
function SettingRow({
  label,
  value,
  editLabel,
  field,
  placeholder,
  save,
  onChange,
}: {
  label: string;
  value: string;
  editLabel: string;
  field: string;
  placeholder?: string;
  save: (value: string) => Promise<unknown>;
  onChange: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const mutation = useMutation({
    mutationFn: () => save(draft),
    onSuccess: () => {
      setOpen(false);
      onChange();
    },
  });

  return (
    <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
      <div className="flex flex-col">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-medium">{value}</span>
      </div>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (next) {
            setDraft(value);
            mutation.reset();
          }
          setOpen(next);
        }}
      >
        <DialogTrigger asChild>
          <Button variant="outline">{editLabel}</Button>
        </DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editLabel}</DialogTitle>
            <DialogDescription>Saved for the whole Household.</DialogDescription>
          </DialogHeader>
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              mutation.mutate();
            }}
          >
            <label className="flex flex-col gap-1 text-sm">
              {label}
              <input
                className={inputClass}
                placeholder={placeholder}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
              />
              <FieldMessage error={mutation.error} field={field} />
            </label>
            <GeneralError error={mutation.error} />
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={mutation.isPending}>
                Save
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export function HouseholdPage() {
  const queryClient = useQueryClient();
  const { data, error, isPending } = useQuery({ queryKey: ["household"], queryFn: fetchHousehold });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["household"] });
  const [adding, setAdding] = useState(false);

  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <Dialog open={adding} onOpenChange={setAdding}>
        <PageHeader
          title="Household"
          description="The name, time zone and Members everything else belongs to."
          action={
            data && (
              <DialogTrigger asChild>
                <Button>
                  <Plus size={16} />
                  Add Member
                </Button>
              </DialogTrigger>
            )
          }
        />
        {isPending && <p className="text-muted-foreground">Loading…</p>}
        {error && <p className="text-destructive">Could not load the Household: {error.message}</p>}
        {data && (
          <>
            <Card>
              <CardHeader>
                <CardTitle>Details</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                <SettingRow
                  label="Household name"
                  value={data.name}
                  editLabel="Rename Household"
                  field="name"
                  save={renameHousehold}
                  onChange={refresh}
                />
                <SettingRow
                  label="Time zone"
                  value={data.time_zone}
                  editLabel="Change time zone"
                  field="time_zone"
                  placeholder="Australia/Melbourne"
                  save={(tz) => setTimeZone(data.name, tz)}
                  onChange={refresh}
                />
              </CardContent>
            </Card>
            <section aria-label="Members" className="flex flex-col gap-2">
              <h2 className="text-lg font-semibold">Members</h2>
              {data.members.length === 0 ? (
                <p className="text-sm text-muted-foreground">No Members yet. Add the first one.</p>
              ) : (
                <Table aria-label="Members">
                  <TableHeader>
                    <TableRow>
                      <TableHead>Name</TableHead>
                      <TableHead>Date of birth</TableHead>
                      <TableHead>
                        <span className="sr-only">Actions</span>
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.members.map((m) => (
                      <MemberRow key={m.id} member={m} onChange={refresh} />
                    ))}
                  </TableBody>
                </Table>
              )}
            </section>
          </>
        )}
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add Member</DialogTitle>
            <DialogDescription>A person in the Household who can own Accounts.</DialogDescription>
          </DialogHeader>
          <MemberForm
            initial={{ name: "", date_of_birth: "" }}
            submitLabel="Add Member"
            onSubmit={addMember}
            onCancel={() => setAdding(false)}
            onDone={() => {
              setAdding(false);
              refresh();
            }}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}
