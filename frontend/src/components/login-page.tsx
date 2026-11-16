import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { GeneralError, inputClass } from "@/components/form-fields";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { logIn } from "@/lib/auth";

export function LoginPage({ totpRequired, onDone }: { totpRequired: boolean; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [trustDevice, setTrustDevice] = useState(false);
  const signIn = useMutation({ mutationFn: logIn, onSuccess: onDone });

  return (
    <Card className="mx-auto w-full max-w-sm">
      <CardHeader>
        <CardTitle>Sign in</CardTitle>
      </CardHeader>
      <CardContent>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            signIn.mutate({ password, totp: code, trustDevice });
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            Password
            <input
              className={inputClass}
              type="password"
              autoComplete="current-password"
              autoFocus
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {totpRequired && (
            <label className="flex flex-col gap-1 text-sm">
              Authenticator code
              <input
                className={inputClass}
                inputMode="numeric"
                autoComplete="one-time-code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
              />
            </label>
          )}
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={trustDevice}
              onChange={(e) => setTrustDevice(e.target.checked)}
            />
            Keep me signed in on this device
          </label>
          <GeneralError error={signIn.error} />
          <Button type="submit" disabled={signIn.isPending}>
            Sign in
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
