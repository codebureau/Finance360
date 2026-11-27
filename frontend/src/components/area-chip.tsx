import { useRouter, useRouterState } from "@tanstack/react-router";

import { SIDEBAR_GROUPS, type GroupTone, type Item } from "@/components/sidebar-nav";
import { TOKEN_TEXT, TOKEN_TINT } from "@/lib/entity-colour";
import { cn } from "@/lib/utils";

/** The sidebar item the path belongs to: the longest match (`/accounts/4` is Accounts), exact
 * items only on their own path. */
export function areaOf(pathname: string): { item: Item; tone: GroupTone; heading: string } | null {
  let best: { item: Item; tone: GroupTone; heading: string } | null = null;
  for (const group of SIDEBAR_GROUPS) {
    for (const item of group.items) {
      const hit = pathname === item.to || (!item.exact && pathname.startsWith(`${item.to}/`));
      if (hit && (!best || item.to.length > best.item.to.length)) best = { item, tone: group.tone, heading: group.heading };
    }
  }
  return best;
}

/**
 * The small icon chip beside a page title: the sidebar item's icon in its group's colour
 * (ADR-0031 section 5). A wayfinding cue only, never a fill or a chart colour; the title says the
 * same in words.
 */
export function AreaChip() {
  // A page shown outside the router (a test of one component) has no area, so no chip.
  const router = useRouter({ warn: false });
  return router ? <RoutedAreaChip /> : null;
}

function RoutedAreaChip() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const area = areaOf(pathname);
  if (!area) return null;
  const Icon = area.item.icon;
  return (
    <span
      aria-hidden
      data-testid="area-chip"
      className={cn(
        "flex size-8 shrink-0 items-center justify-center rounded-md",
        area.tone === null ? "bg-muted text-muted-foreground" : cn(TOKEN_TINT[area.tone], TOKEN_TEXT[area.tone]),
      )}
    >
      <Icon size={18} />
    </span>
  );
}
