import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError } from "@/lib/household";
import { setUp } from "@/lib/auth";

export function SetupPage({ onDone }: { onDone: () => void }) {
  const [setupCode, setSetupCode] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [trustDevice, setTrustDevice] = useState(false);
  const setup = useMutation({
    mutationFn: async () => {
      if (password !== repeat) throw new ApiError("", { repeat: "The two passwords differ." });
      return setUp({ setupCode, password, trustDevice });
    },
    onSuccess: onDone,
  });

  return (
    <Card className="mx-auto w-full max-w-md">
      <CardHeader>
        <CardTitle>Set up Finance360</CardTitle>
        <p className="text-sm text-muted-foreground">
          Choose the password for this household. The setup code is in the file{" "}
          <code className="font-mono">setup-code</code> in the data directory on the host (with Docker:{" "}
          <code className="font-mono">docker compose exec finance360 cat /data/setup-code</code>).
        </p>
      </CardHeader>
      <CardContent>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            setup.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            Setup code
            <input
              className={inputClass}
              autoComplete="off"
              autoFocus
              value={setupCode}
              onChange={(e) => setSetupCode(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            New password (12 to 128 characters)
            <input
              className={inputClass}
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <FieldMessage error={setup.error} field="password" />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Repeat the password
            <input
              className={inputClass}
              type="password"
              autoComplete="new-password"
              value={repeat}
              onChange={(e) => setRepeat(e.target.value)}
            />
            <FieldMessage error={setup.error} field="repeat" />
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={trustDevice}
              onChange={(e) => setTrustDevice(e.target.checked)}
            />
            Keep me signed in on this device
          </label>
          <GeneralError error={setup.error} />
          <Button type="submit" disabled={setup.isPending}>
            Create password and sign in
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
