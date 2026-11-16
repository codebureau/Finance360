import { Link } from "@tanstack/react-router";

import { DensityToggle } from "@/components/density-toggle";
import { SecurityPage } from "@/components/security-page";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";

/** Settings: appearance (density) and Security. The theme toggle lives in the sidebar. */
export function SettingsPage() {
  return (
    <div className="flex max-w-2xl flex-col gap-6">
      <PageHeader title="Settings" description="Appearance, the AI log, Document profiles, your sign-in and security." />
      <Card>
        <CardHeader>
          <CardTitle>Density</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-sm text-muted-foreground">
            Compact tightens table rows and controls to fit more on screen.
          </p>
          <DensityToggle />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>AI log</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-sm text-muted-foreground">
            Every question Ask has sent to the AI provider, exactly as it left Finance360, and what came back.
          </p>
          <Link to="/settings/ai-log" className="w-fit text-sm text-primary underline underline-offset-4">
            Open the AI log
          </Link>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Document profiles</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-sm text-muted-foreground">
            How Finance360 recognises a kind of statement and reads figures from it: the ones that ship with it, and
            any you add.
          </p>
          <Link
            to="/settings/document-profiles"
            className="w-fit text-sm text-primary underline underline-offset-4"
          >
            Open Document profiles
          </Link>
        </CardContent>
      </Card>
      <SecurityPage />
    </div>
  );
}
