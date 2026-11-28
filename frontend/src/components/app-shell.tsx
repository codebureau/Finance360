import { Link } from "@tanstack/react-router";
import { Menu } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Logo } from "@/components/logo";
import { ThemeToggle } from "@/components/theme-toggle";
import { ReturnBar } from "@/components/return-bar";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

/** The Finance360 logo: a link home (`/`). */
function Brand({ className }: { className: string }) {
  return (
    <Link to="/" className={className}>
      <Logo />
    </Link>
  );
}

/**
 * The page frame (ADR-0014): a fixed sidebar from `md` up, a top bar with a menu Sheet below it.
 * Slots from the first shell are kept: `nav` (section navigation), `actions` (beside the theme toggle) and
 * `footer` (status), all placed at the sidebar and repeated in the mobile Sheet. The page content
 * is `data-testid="content"`.
 */
export function AppShell({
  nav,
  ask,
  dock,
  actions,
  footer,
  children,
}: {
  nav: ReactNode;
  /** The Ask button: top of the sidebar, and beside the Menu button in the mobile top bar. */
  ask?: ReactNode;
  /** A panel docked to the right of the page on wide screens (the Ask panel); it renders nothing when closed. */
  dock?: ReactNode;
  actions?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const foot = (
    <div className="flex flex-col gap-3 text-xs">
      {footer}
      <div className="flex flex-wrap items-center gap-2">
        {actions}
        <ThemeToggle />
      </div>
    </div>
  );

  return (
    <div className="min-h-screen md:flex">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-border bg-card text-card-foreground md:sticky md:top-0 md:flex md:h-screen">
        <div className="flex items-center justify-between gap-2 px-4 py-4">
          <Brand className="text-xl font-bold" />
          {ask}
        </div>
        <div className="flex-1 overflow-y-auto px-3">{nav}</div>
        <div className="border-t border-border p-3">{foot}</div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-40 flex items-center justify-between border-b border-border bg-card px-4 py-2 text-card-foreground md:hidden">
          <Brand className="text-lg font-bold" />
          <div className="flex items-center gap-2">
          {ask}
            <Sheet open={menuOpen} onOpenChange={setMenuOpen}>
              <SheetTrigger asChild>
                <Button variant="outline" aria-label="Open menu">
                  <Menu size={16} />
                  Menu
                </Button>
              </SheetTrigger>
              <SheetContent
                side="left"
                // Navigation holds nothing to lose, so a tap on the scrim closes it.
                dismissOnOutsideClick
                aria-describedby={undefined}
                // Following a link in the menu closes it.
                onClick={(e) => {
                  if ((e.target as Element).closest("a")) setMenuOpen(false);
                }}
              >
                <SheetTitle className="text-xl font-bold">
                  <Brand className="" />
                </SheetTitle>
                <SheetDescription className="sr-only">Sections, status and account controls</SheetDescription>
                <div className="flex-1">{nav}</div>
                <div className="border-t border-border pt-3">{foot}</div>
              </SheetContent>
            </Sheet>
          </div>
        </header>
        <main data-testid="content" className="mx-auto w-full max-w-7xl min-w-0 flex-1 px-4 py-6 sm:px-6">
          <ReturnBar />
          {children}
        </main>
      </div>
      {dock}
    </div>
  );
}
