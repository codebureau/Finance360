import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import { useState, type ReactNode } from "react";

import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
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
  beginTotp,
  changePassword,
  confirmTotp,
  disableTotp,
  fetchSecurity,
  type TotpBegin,
} from "@/lib/auth";
import { ApiError } from "@/lib/household";

/** Scan the QR code, then confirm with a code from the app. */
function ConfirmForm({ started, onDone }: { started: TotpBegin; onDone: () => void }) {
  const [code, setCode] = useState("");
  const confirm = useMutation({ mutationFn: confirmTotp, onSuccess: onDone });
  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        confirm.mutate(code);
      }}
    >
      <p className="text-sm">Scan this with your authenticator app, then enter the code it shows.</p>
      {/* A QR code must stay dark on white to scan, in either theme. */}
      {/* eslint-disable-next-line no-restricted-syntax */}
      <div className="w-fit rounded-md bg-white p-3">
        <QRCodeSVG value={started.uri} size={168} title="Authenticator QR code" />
      </div>
      <p className="text-sm text-muted-foreground">
        Or enter this key by hand: <code className="font-mono break-all select-all">{started.secret}</code>
      </p>
      <label className="flex flex-col gap-1 text-sm">
        Code from the app
        <input
          className={inputClass}
          inputMode="numeric"
          autoComplete="one-time-code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
        <FieldMessage error={confirm.error} field="code" />
      </label>
      <GeneralError error={confirm.error} />
      <div>
        <Button type="submit" disabled={confirm.isPending}>
          Confirm and turn on
        </Button>
      </div>
    </form>
  );
}

/** Inline enrol, used only on the one-off screen right after first-run setup. */
function Enrol({ onDone }: { onDone: () => void }) {
  const begin = useMutation({ mutationFn: beginTotp });
  const started: TotpBegin | undefined = begin.data;

  if (!started) {
    return (
      <div className="flex flex-col gap-2">
        <p className="text-sm text-muted-foreground">
          Sign-in then asks for a 6-digit code from an authenticator app as well as the password.
        </p>
        <div>
          <Button onClick={() => begin.mutate()} disabled={begin.isPending}>
            Set up an authenticator app
          </Button>
        </div>
        <GeneralError error={begin.error} />
      </div>
    );
  }
  return <ConfirmForm started={started} onDone={onDone} />;
}

/** Right after first-run setup: offer the authenticator once, inline. */
export function TotpSection() {
  const queryClient = useQueryClient();
  const { data, error } = useQuery({ queryKey: ["security"], queryFn: fetchSecurity });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["security"] });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Authenticator app</CardTitle>
      </CardHeader>
      <CardContent>
        {error && <p className="text-destructive">Could not load: {error.message}</p>}
        {data &&
          (data.totp_enabled ? (
            <p className="text-sm text-muted-foreground">An authenticator app is on.</p>
          ) : (
            <Enrol onDone={refresh} />
          ))}
      </CardContent>
    </Card>
  );
}

function PasswordField({
  label,
  autoComplete,
  value,
  onChange,
  error,
  field,
}: {
  label: string;
  autoComplete: string;
  value: string;
  onChange: (v: string) => void;
  error: unknown;
  field: string;
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      {label}
      <input
        className={inputClass}
        type="password"
        autoComplete={autoComplete}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <FieldMessage error={error} field={field} />
    </label>
  );
}

function ChangePasswordDialog({ onChanged }: { onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const change = useMutation({
    mutationFn: async () => {
      if (next !== repeat) throw new ApiError("", { repeat: "The two passwords differ." });
      await changePassword(current, next);
    },
    onSuccess: () => {
      setOpen(false);
      onChanged();
    },
  });
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) {
          setCurrent("");
          setNext("");
          setRepeat("");
          change.reset();
        }
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline">Change password</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Change password</DialogTitle>
          <DialogDescription>Your other devices are signed out when it changes.</DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            change.mutate();
          }}
        >
          <PasswordField
            label="Current password"
            autoComplete="current-password"
            value={current}
            onChange={setCurrent}
            error={change.error}
            field="current_password"
          />
          <PasswordField
            label="New password (12 to 128 characters)"
            autoComplete="new-password"
            value={next}
            onChange={setNext}
            error={change.error}
            field="new_password"
          />
          <PasswordField
            label="Repeat the new password"
            autoComplete="new-password"
            value={repeat}
            onChange={setRepeat}
            error={change.error}
            field="repeat"
          />
          <GeneralError error={change.error} />
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={change.isPending}>
              Change password
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function EnrolDialog({ onDone }: { onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const begin = useMutation({ mutationFn: beginTotp });
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) begin.reset();
      }}
    >
      <Button
        variant="outline"
        disabled={begin.isPending}
        onClick={() => {
          begin.mutate();
          setOpen(true);
        }}
      >
        Set up an authenticator app
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Set up an authenticator app</DialogTitle>
          <DialogDescription>
            Sign-in then asks for a 6-digit code from the app as well as the password.
          </DialogDescription>
        </DialogHeader>
        {begin.isPending && <p className="text-sm text-muted-foreground">Preparing…</p>}
        <GeneralError error={begin.error} />
        {begin.data && (
          <ConfirmForm
            started={begin.data}
            onDone={() => {
              setOpen(false);
              onDone();
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

/** Turning the authenticator off is destructive: an AlertDialog that also takes the credentials. */
function DisableDialog({ onDone }: { onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const disable = useMutation({
    mutationFn: () => disableTotp(password, code),
    onSuccess: () => {
      setOpen(false);
      onDone();
    },
  });
  return (
    <AlertDialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) {
          setPassword("");
          setCode("");
          disable.reset();
        }
      }}
    >
      <AlertDialogTrigger asChild>
        <Button variant="destructive">Turn off authenticator</Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Turn off the authenticator app?</AlertDialogTitle>
          <AlertDialogDescription>
            Sign-in will ask for the password only. Confirm with your password and a current code.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            disable.mutate();
          }}
        >
          <PasswordField
            label="Password"
            autoComplete="current-password"
            value={password}
            onChange={setPassword}
            error={null}
            field="password"
          />
          <label className="flex flex-col gap-1 text-sm">
            Current code
            <input
              className={inputClass}
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
            <FieldMessage error={disable.error} field="code" />
          </label>
          <GeneralError error={disable.error} />
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button type="submit" variant="destructive" disabled={disable.isPending}>
              Turn off
            </Button>
          </AlertDialogFooter>
        </form>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function SecurityRow({
  title,
  description,
  status,
  children,
}: {
  title: string;
  description: string;
  status?: ReactNode;
  children: ReactNode;
}) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 p-4">
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex items-center gap-2">
          <h4 className="font-medium">{title}</h4>
          {status}
        </div>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      <div className="shrink-0">{children}</div>
    </li>
  );
}

/** Settings > Security: Password and the authenticator, each edited in a dialog. */
export function SecurityPage() {
  const queryClient = useQueryClient();
  const { data, error } = useQuery({ queryKey: ["security"], queryFn: fetchSecurity });
  const [changed, setChanged] = useState(false);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["security"] });
  return (
    <section aria-labelledby="security-heading" className="flex flex-col gap-3">
      <h3 id="security-heading" className="text-lg font-semibold">
        Security
      </h3>
      {error && <p className="text-destructive">Could not load: {error.message}</p>}
      {changed && (
        <p role="status" className="text-sm text-success">
          Password changed. Your other devices have been signed out.
        </p>
      )}
      <ul className="divide-y divide-border rounded-lg border border-border bg-card">
        <SecurityRow title="Password" description="Used with every sign-in.">
          <ChangePasswordDialog onChanged={() => setChanged(true)} />
        </SecurityRow>
        {data && (
          <SecurityRow
            title="Authenticator app"
            description={
              data.totp_enabled
                ? "Sign-in asks for a 6-digit code as well as the password."
                : "Add a 6-digit code from an authenticator app to sign-in."
            }
            status={
              <Badge variant={data.totp_enabled ? "ok" : "closed"}>{data.totp_enabled ? "On" : "Off"}</Badge>
            }
          >
            {data.totp_enabled ? <DisableDialog onDone={refresh} /> : <EnrolDialog onDone={refresh} />}
          </SecurityRow>
        )}
      </ul>
    </section>
  );
}
