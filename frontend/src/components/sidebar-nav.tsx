import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import {
  Archive,
  Banknote,
  BookOpen,
  Building2,
  Calculator,
  CloudDownload,
  FileCheck,
  FileDown,
  FlaskConical,
  ClipboardList,
  History,
  Home,
  Layers,
  Library,
  LineChart,
  LayoutDashboard,
  MessageCircleQuestion,
  PieChart,
  Receipt,
  Scale,
  TrendingUp,
  Settings,
  Tags,
  Wallet,
  type LucideIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { TOKEN_TEXT } from "@/lib/entity-colour";
import { fetchWhatsDue, importantCount, WHATS_DUE_KEY } from "@/lib/due";
import { fetchQuestions, QUESTIONS_KEY, type Question } from "@/lib/questions";
import { cn } from "@/lib/utils";

/** `exact`: not active on the screens below it (`/tax` is not `/tax/records`). */
export type Item = { to: string; label: string; icon: LucideIcon; exact?: boolean };

/** Which of the six chart tokens tints a group's icons and its page-title chip (ADR-0031 section 5);
 * a wayfinding cue only. `null`: muted. */
export type GroupTone = number | null;

/** Above the groups: the home screen (ADR-0020) and the Guide. */
export const TOP_ITEMS: Item[] = [
  { to: "/", label: "Home", icon: LayoutDashboard, exact: true },
  { to: "/recent-changes", label: "Recent changes", icon: History, exact: true },
  { to: "/guide", label: "Guide", icon: BookOpen, exact: true },
];

/**
 * The sidebar groups by the question being asked, not by kind of data. Each heading links to
 * the Guide page that covers its group (`guide`), so every group says where to read more.
 */
export const SIDEBAR_GROUPS: { heading: string; guide: string; tone: GroupTone; items: Item[] }[] = [
  {
    heading: "Where we stand",
    guide: "/guide/position",
    tone: 0,
    items: [
      { to: "/position", label: "Position", icon: LineChart },
      { to: "/exposure", label: "Exposure", icon: Layers },
    ],
  },
  {
    heading: "Where we're heading",
    guide: "/guide/projection",
    tone: 3,
    items: [{ to: "/projection", label: "Projection", icon: TrendingUp }],
  },
  {
    heading: "This tax year",
    guide: "/guide/tax",
    tone: 5,
    items: [
      { to: "/tax", label: "Tax estimate", icon: Calculator, exact: true },
      { to: "/tax/reconciliation", label: "Income reconciliation", icon: PieChart },
      { to: "/tax/records", label: "Tax records", icon: Receipt },
      { to: "/tax/whatifs", label: "Tax what-ifs", icon: FlaskConical },
      { to: "/tax/estimates", label: "Saved estimates", icon: Archive },
      { to: "/tax/lodged", label: "Return worksheet", icon: FileCheck },
    ],
  },
  {
    heading: "Keep it current",
    guide: "/guide/accounts-and-imports",
    tone: 2,
    items: [
      { to: "/accounts", label: "Accounts", icon: Wallet },
      { to: "/imports", label: "Imports", icon: FileDown },
      { to: "/income-streams", label: "Income streams", icon: Banknote },
      { to: "/household", label: "Household", icon: Home },
    ],
  },
  {
    heading: "Your documents",
    guide: "/guide/library",
    tone: 1,
    items: [{ to: "/library", label: "Library", icon: Library }],
  },
  {
    heading: "Reference and system",
    guide: "/guide/what-to-provide",
    tone: null,
    items: [
      { to: "/instruments", label: "Instruments", icon: Building2 },
      { to: "/fx-rates", label: "FX rates", icon: Banknote },
      { to: "/reference-data", label: "Reference data", icon: CloudDownload },
      { to: "/rule-figures", label: "Rule figures", icon: Scale },
      { to: "/categories", label: "Categories", icon: Tags },
      { to: "/audit", label: "Audit trail", icon: ClipboardList },
      { to: "/settings", label: "Settings", icon: Settings },
    ],
  },
];

const ITEM_CLASS = cn(
  "flex min-h-[var(--control-h)] items-center gap-2 rounded-md px-2 text-sm transition-colors hover:bg-accent",
  "aria-[current=page]:bg-accent aria-[current=page]:font-medium aria-[current=page]:text-accent-foreground",
);

function NavLink({ item, important, tone }: { item: Item; important: number; tone?: GroupTone }) {
  const { to, label, icon: Icon, exact } = item;
  return (
    <Link to={to} activeOptions={{ exact: exact ?? false }} className={ITEM_CLASS}>
      <Icon size={16} aria-hidden className={tone === undefined ? undefined : tone === null ? "text-muted-foreground" : TOKEN_TEXT[tone]} />
      {label}
      {to === "/" && important > 0 && (
        <Badge variant="info" className="ml-auto" aria-label={`${important} important`}>
          {important}
        </Badge>
      )}
    </Link>
  );
}

/** The heading of a group, linking to the Guide page that covers it. */
function GroupHeading({ heading, guide }: { heading: string; guide: string }) {
  return (
    <div className="flex items-center justify-between gap-2 pl-2">
      <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{heading}</h2>
      <Link
        to={guide}
        aria-label={`Guide: ${heading}`}
        title="Read about this in the Guide"
        className="flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground aria-[current=page]:text-foreground"
      >
        <BookOpen size={14} aria-hidden />
      </Link>
    </div>
  );
}

/** Where the heading of "Your questions" reads more (the Guide page "What Finance360 answers"). */
export const QUESTIONS_GUIDE = "/guide/what-finance360-answers";

/**
 * "Your questions" (ADR-0032 section 3): one item per registered Question, in registry order, built
 * from the API so a new Question needs no edit here. Each icon takes the colour of the group its
 * screen sits in (`area` is that group's heading). Nothing renders until there are Questions.
 */
function QuestionsGroup({ questions }: { questions: Question[] }) {
  if (questions.length === 0) return null;
  return (
    <div className="flex flex-col gap-1" data-testid="questions-group">
      <GroupHeading heading="Your questions" guide={QUESTIONS_GUIDE} />
      <ul className="flex flex-col gap-0.5">
        {questions.map((q) => {
          const tone = SIDEBAR_GROUPS.find((g) => g.heading === q.area)?.tone ?? null;
          return (
            <li key={q.key}>
              <Link
                to="/questions/$questionKey"
                params={{ questionKey: q.key }}
                className={cn(ITEM_CLASS, "items-start py-1.5")}
              >
                <MessageCircleQuestion
                  size={16}
                  aria-hidden
                  className={cn("mt-0.5 shrink-0", tone === null ? "text-muted-foreground" : TOKEN_TEXT[tone])}
                />
                <span className="min-w-0 break-words">{q.wording}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Section navigation. The router marks the active link with `aria-current="page"`. */
export function SidebarNav() {
  // The count is important items only, and it is the same query the home screen uses.
  const due = useQuery({ queryKey: WHATS_DUE_KEY, queryFn: fetchWhatsDue });
  const important = importantCount(due.data);
  const questions = useQuery({ queryKey: QUESTIONS_KEY, queryFn: fetchQuestions });
  return (
    <nav aria-label="Sections" className="flex flex-col gap-4">
      <ul className="flex flex-col gap-0.5">
        {TOP_ITEMS.map((item) => (
          <li key={item.to}>
            <NavLink item={item} important={important} />
          </li>
        ))}
      </ul>
      <QuestionsGroup questions={questions.data?.questions ?? []} />
      {SIDEBAR_GROUPS.map((group) => (
        <div key={group.heading} className="flex flex-col gap-1">
          <GroupHeading heading={group.heading} guide={group.guide} />
          <ul className="flex flex-col gap-0.5">
            {group.items.map(({ to, label, icon: Icon, exact }) => (
              <li key={to}>
                <NavLink item={{ to, label, icon: Icon, exact }} important={important} tone={group.tone} />
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}
