import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";

import { LoginPage } from "@/components/login-page";
import { Logo } from "@/components/logo";
import { TotpSection } from "@/components/security-page";
import { SetupPage } from "@/components/setup-page";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { fetchAuthStatus } from "@/lib/auth";
import { SIGNED_OUT_EVENT } from "@/lib/household";

const STATUS_KEY = ["auth-status"];

/** Shows setup or sign-in until there is a Session, then the app (default deny, ADR-0010). */
export function AuthGate({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const { data, error, isPending } = useQuery({ queryKey: STATUS_KEY, queryFn: fetchAuthStatus });
  const [justSetUp, setJustSetUp] = useState(false);
  const refresh = () => queryClient.invalidateQueries({ queryKey: STATUS_KEY });

  // Any request refused as signed out (an expired Session) sends us back to the login page.
  useEffect(() => {
    const onSignedOut = () => void queryClient.invalidateQueries({ queryKey: STATUS_KEY });
    window.addEventListener(SIGNED_OUT_EVENT, onSignedOut);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, onSignedOut);
  }, [queryClient]);

  let body: ReactNode;
  if (isPending) {
    body = <p className="text-muted-foreground">Checking…</p>;
  } else if (error) {
    body = <p className="text-destructive">Unreachable: {error.message}</p>;
  } else if (data.setup_required) {
    body = (
      <SetupPage
        onDone={() => {
          setJustSetUp(true);
          void refresh();
        }}
      />
    );
  } else if (!data.authenticated) {
    body = <LoginPage totpRequired={data.totp_required} onDone={() => void refresh()} />;
  } else if (justSetUp) {
    body = (
      <>
        <Card>
          <CardHeader>
            <CardTitle>You are signed in</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <p className="text-sm text-muted-foreground">
              You can add an authenticator app now, or later under Security.
            </p>
            <div>
              <Button onClick={() => setJustSetUp(false)}>Continue to Finance360</Button>
            </div>
          </CardContent>
        </Card>
        <TotpSection />
      </>
    );
  } else {
    return <>{children}</>;
  }
  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col gap-6 p-6">
      <Logo className="self-center text-2xl font-bold" markClassName="size-9" />
      {body}
    </main>
  );
}
