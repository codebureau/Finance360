import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useRouterState } from "@tanstack/react-router";
import { MessageCircleQuestion, X } from "lucide-react";
import { createContext, useContext, useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";

import { GeneralError, inputClass } from "@/components/form-fields";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import {
  AI_STATUS_KEY,
  QUESTION_MAX,
  answerPausedText,
  appendExchange,
  fetchAiStatus,
  searchGuide,
  writeAnswer,
  type AiStatus,
  type AskSearch,
  type AskSource,
  type Exchange,
  type Whereabouts,
} from "@/lib/ask";
import { START_SLUG, findGuidePage } from "@/lib/guide";
import { ApiError } from "@/lib/household";

type AskControl = { open: boolean; setOpen: (open: boolean) => void };
const AskContext = createContext<AskControl | null>(null);

/** Whether the Ask panel is open. The panel and its buttons share it; the shell mounts one provider. */
export function AskProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return <AskContext.Provider value={{ open, setOpen }}>{children}</AskContext.Provider>;
}

function useAsk(): AskControl {
  const control = useContext(AskContext);
  if (!control) throw new Error("Ask controls need an AskProvider.");
  return control;
}

/**
 * The shell's Ask button: on every screen, on or off (an off panel points to the Guide instead). It
 * opens the panel, and closes it again when it is docked beside the page.
 */
export function AskButton({ className }: { className?: string }) {
  const { open, setOpen } = useAsk();
  return (
    <Button variant="outline" className={className} aria-expanded={open} onClick={() => setOpen(!open)}>
      <MessageCircleQuestion size={16} aria-hidden />
      Ask
    </Button>
  );
}

/** The route pattern (never an id) and, on a Guide page, its slug: all the server is told about where you are. */
function useWhereabouts(): Whereabouts {
  return useRouterState({
    select: (s): Whereabouts => {
      const match = s.matches[s.matches.length - 1];
      if (!match) return {};
      const screen = match.routeId.startsWith("/") ? match.routeId : undefined;
      const slug = match.routeId === "/guide" ? START_SLUG : (match.params as { slug?: string }).slug;
      const onGuide = match.routeId.startsWith("/guide") && findGuidePage(slug) !== undefined;
      return { screen, guide_slug: onGuide ? slug : undefined };
    },
    structuralSharing: true,
  });
}

function SourceLinks({ sources, onFollow }: { sources: AskSource[]; onFollow: () => void }) {
  if (sources.length === 0) return null;
  return (
    <ul aria-label="Sources" className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs">
      <li className="text-muted-foreground">Sources:</li>
      {sources.map((s) => (
        <li key={`${s.kind}:${s.ref}`}>
          <Link to={s.link} onClick={onFollow} className="text-primary underline underline-offset-4">
            {s.label}
          </Link>
        </li>
      ))}
    </ul>
  );
}

/** The best Passages for a question: each links to the Guide page it came from. */
function Passages({ search, onFollow }: { search: AskSearch; onFollow: () => void }) {
  if (!search.found) {
    return (
      <div className="flex flex-col gap-1" data-testid="nothing-found">
        <p>{search.message}</p>
        {search.start && <SourceLinks sources={[search.start]} onFollow={onFollow} />}
      </div>
    );
  }
  return (
    <ol aria-label="Passages from the Guide" className="flex flex-col gap-2">
      {search.passages.map((p, n) => (
        <li key={`${p.page}:${n}`} className="flex flex-col gap-0.5 rounded-md border border-border p-2">
          <Link to={p.link} onClick={onFollow} className="font-medium text-primary underline underline-offset-4">
            {p.label}
          </Link>
          {p.heading !== p.label && <span className="text-xs text-muted-foreground">{p.heading}</span>}
          <span className="break-words">{p.excerpt}</span>
        </li>
      ))}
    </ol>
  );
}

function Thread({
  thread,
  pending,
  answering,
  onFollow,
}: {
  thread: Exchange[];
  pending: string | null;
  answering: boolean;
  onFollow: () => void;
}) {
  const end = useRef<HTMLDivElement>(null);
  const last = useRef<HTMLDivElement>(null);
  const writing = thread.some((e) => e.writing === "pending");
  useEffect(() => {
    const el = end.current?.parentElement;
    if (!el) return;
    if (pending !== null || !last.current) {
      el.scrollTop = el.scrollHeight;
      return;
    }
    // Show the newest exchange from its top, so a written answer that arrives above the passages is
    // seen without scrolling back up.
    el.scrollTop += last.current.getBoundingClientRect().top - el.getBoundingClientRect().top;
  }, [thread.length, pending, writing]);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto" aria-label="Conversation" role="log">
      {thread.length === 0 && pending === null && (
        <p className="text-sm text-muted-foreground">
          {answering
            ? "Ask about Finance360, for example “Where do I enter my salary?” or “What should I do next?”. You get the best passages from the Guide at once, then an answer written from them. Answers can be wrong: check the page they cite."
            : "Search the Guide in plain words, for example “Where do I enter my salary?” or “What is a Rule figure?”. You get the best matching passages, each with a link to the page. It points; it does not work anything out for you."}
        </p>
      )}
      {thread.map((e, n) => (
        <div key={e.id} ref={n === thread.length - 1 ? last : undefined} className="flex flex-col gap-2">
          <p className="ml-auto max-w-[90%] rounded-lg bg-muted px-3 py-2 text-sm break-words whitespace-pre-wrap">
            {e.question}
          </p>
          <div className="flex max-w-[95%] flex-col gap-3 text-sm">
            {e.answer && (
              <div data-testid="written-answer">
                <p className="break-words whitespace-pre-wrap">{e.answer.answer}</p>
                {e.answer.answered && e.answer.model && (
                  <p className="text-xs text-muted-foreground">Written by {e.answer.model} from the passages below.</p>
                )}
                <SourceLinks sources={e.answer.sources} onFollow={onFollow} />
              </div>
            )}
            {e.writing === "pending" && (
              <p role="status" className="text-muted-foreground">
                Writing an answer from the passages below&hellip; this can take a while.
              </p>
            )}
            {e.writing === "failed" && (
              <p className="text-xs text-muted-foreground" data-testid="answer-failed">
                A written answer isn&apos;t available just now{e.note ? ` (${e.note})` : ""}. The passages below are the best
                matches.
              </p>
            )}
            <Passages search={e.search} onFollow={onFollow} />
          </div>
        </div>
      ))}
      {pending !== null && (
        <div className="flex flex-col gap-2">
          <p className="ml-auto max-w-[90%] rounded-lg bg-muted px-3 py-2 text-sm break-words whitespace-pre-wrap">
            {pending}
          </p>
          <p role="status" className="text-sm text-muted-foreground">
            Searching the Guide&hellip;
          </p>
        </div>
      )}
      <div ref={end} />
    </div>
  );
}

function PanelBody({
  thread,
  pending,
  error,
  draft,
  setDraft,
  status,
  onAsk,
  onNew,
  onFollow,
}: {
  thread: Exchange[];
  pending: string | null;
  error: unknown;
  draft: string;
  setDraft: (draft: string) => void;
  status: AiStatus | undefined;
  onAsk: (question: string, done: () => void) => void;
  onNew: () => void;
  onFollow: () => void;
}) {
  // Search needs no model, so the form never waits for the status: it only says which level this is.
  const answering = status?.answering ?? false;
  const paused = status !== undefined && status.answering && !status.enabled;
  const busy = pending !== null || thread.some((e) => e.writing === "pending");
  const fieldError = error instanceof ApiError ? (error.fields.question ?? error.fields.history) : undefined;

  return (
    <>
      <Thread thread={thread} pending={pending} answering={answering} onFollow={onFollow} />
      {paused && (
        <p className="text-xs text-muted-foreground" data-testid="answers-paused">
          {answerPausedText(status)}
        </p>
      )}
      <form
        aria-label="Ask a question"
        className="flex flex-col gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (draft.trim() && !busy) onAsk(draft.trim(), () => setDraft(""));
        }}
      >
        <label className="flex flex-col gap-1 text-sm">
          Your question
          <textarea
            className={`${inputClass} py-2`}
            rows={3}
            maxLength={QUESTION_MAX}
            value={draft}
            disabled={busy}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends, as in a chat; Shift+Enter or Ctrl+Enter starts a new line. Not mid-IME.
              if (e.key !== "Enter" || e.nativeEvent.isComposing || e.altKey || e.metaKey) return;
              if (e.shiftKey) return;
              e.preventDefault();
              const box = e.currentTarget;
              if (e.ctrlKey) {
                const { selectionStart: start, selectionEnd: end } = box;
                if (draft.length - (end - start) >= QUESTION_MAX) return;
                setDraft(`${draft.slice(0, start)}\n${draft.slice(end)}`);
                requestAnimationFrame(() => box.setSelectionRange(start + 1, start + 1));
                return;
              }
              box.form?.requestSubmit();
            }}
          />
          <span className="text-xs text-muted-foreground">
            {draft.length} of {QUESTION_MAX} characters. Enter sends; Shift+Enter starts a new line.
          </span>
        </label>
        {fieldError && <p className="text-sm text-destructive">{fieldError}</p>}
        <GeneralError error={error} />
        <div className="flex flex-wrap justify-between gap-2">
          <Button type="button" variant="ghost" disabled={busy || (thread.length === 0 && !error)} onClick={onNew}>
            New question
          </Button>
          <Button type="submit" disabled={busy || draft.trim() === ""}>
            {answering ? "Ask" : "Search"}
          </Button>
        </div>
      </form>
      {answering ? (
        <p className="border-t border-border pt-3 text-xs text-muted-foreground">
          Searching stays on your server. Writing an answer sends your question and the best passages to an AI service,
          with account names and numbers removed first.{" "}
          <Link to="/settings/ai-log" onClick={onFollow} className="text-primary underline underline-offset-4">
            See what was sent
          </Link>
        </p>
      ) : (
        <p className="border-t border-border pt-3 text-xs text-muted-foreground">
          Searches this Guide on your server. Nothing is sent anywhere.
        </p>
      )}
    </>
  );
}
/** Wide enough to dock the panel beside the page (Tailwind `lg`); narrower, it is a Sheet over it. */
const DOCK_QUERY = "(min-width: 1024px)";

function useDocked(): boolean {
  return useSyncExternalStore(
    (changed) => {
      const media = window.matchMedia?.(DOCK_QUERY);
      media?.addEventListener("change", changed);
      return () => media?.removeEventListener("change", changed);
    },
    () => window.matchMedia?.(DOCK_QUERY).matches ?? false,
  );
}

const DESCRIPTION_SEARCH =
  "Searches the Guide on your server and shows the best passages, with links. It points; it never changes anything.";
const DESCRIPTION_ANSWERING =
  "Questions about Finance360, answered from the Guide and What's due. It explains and points; it never changes anything.";

/**
 * The Ask panel (ADR-0021). On a wide screen it docks beside the page: nothing is dimmed and the page
 * stays usable, so you can read a screen while asking about it. On a phone it is a right-hand Sheet.
 * The thread and a typed question live here, in state, so they survive closing the panel and moving
 * between screens, and go with the page on reload; closing never loses anything, so it never asks.
 */
export function AskPanel() {
  const { open, setOpen } = useAsk();
  const docked = useDocked();
  const where = useWhereabouts();
  const queryClient = useQueryClient();
  const [thread, setThread] = useState<Exchange[]>([]);
  const [draft, setDraft] = useState("");
  const nextId = useRef(1);
  // Which level this is (Search only or Written answers), and whether a limit has paused answers.
  // Loaded with the shell so the panel opens already knowing, and asked again each time it opens.
  const status = useQuery({ queryKey: AI_STATUS_KEY, queryFn: () => fetchAiStatus(), staleTime: 0, refetchOnMount: "always" });
  const { refetch: recheck } = status;
  useEffect(() => {
    if (open) void recheck();
  }, [open, recheck]);
  const answering = status.data?.answering ?? false;

  const patch = (id: number, change: Partial<Exchange>) =>
    setThread((t) => t.map((e) => (e.id === id ? { ...e, ...change } : e)));

  /** Step two: the model writes over the Passages the server retrieves again. Search stays on screen either way. */
  const write = (id: number, question: string, before: Exchange[]) => {
    writeAnswer(question, before, where).then(
      (answer) => patch(id, { writing: "done", answer }),
      (error: unknown) => {
        patch(id, { writing: "failed", note: error instanceof Error ? error.message : null });
        // Refused as capped (409): the state moved since the panel opened, so show the real one.
        if (error instanceof ApiError && error.status === 409) void queryClient.invalidateQueries({ queryKey: AI_STATUS_KEY });
      },
    );
  };

  const ask = useMutation({
    mutationFn: async (question: string) => ({ before: thread, search: await searchGuide(question, thread, where) }),
    onSuccess: ({ before, search }, question) => {
      const id = nextId.current++;
      const writes = answering && status.data?.enabled === true && search.found;
      setThread((t) =>
        appendExchange(t, { id, question, search, writing: writes ? "pending" : "none", answer: null, note: null }),
      );
      if (writes) write(id, question, before);
    },
  });

  const body = (
    <PanelBody
      thread={thread}
      pending={ask.isPending ? ask.variables : null}
      error={ask.error}
      draft={draft}
      setDraft={setDraft}
      status={status.data}
      onAsk={(question, done) => ask.mutate(question, { onSuccess: done })}
      onNew={() => {
        setThread([]);
        ask.reset();
      }}
      // Docked, the page beside it follows the link; over a phone screen the panel gets out of the way.
      onFollow={() => {
        if (!docked) setOpen(false);
      }}
    />
  );
  const title = answering ? "Ask" : "Search the Guide";
  const description = answering ? DESCRIPTION_ANSWERING : DESCRIPTION_SEARCH;

  if (docked) {
    if (!open) return null;
    return (
      <aside
        aria-labelledby="ask-panel-title"
        aria-describedby="ask-panel-description"
        className="sticky top-0 flex h-screen w-80 shrink-0 flex-col gap-4 border-l border-border bg-card p-5 text-card-foreground xl:w-96"
      >
        <div className="flex items-start justify-between gap-2">
          <h2 id="ask-panel-title" className="text-lg font-semibold">
            {title}
          </h2>
          <Button variant="ghost" aria-label="Close panel" className="h-auto p-1 text-muted-foreground" onClick={() => setOpen(false)}>
            <X size={16} />
          </Button>
        </div>
        <p id="ask-panel-description" className="text-sm text-muted-foreground">
          {description}
        </p>
        {body}
      </aside>
    );
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetContent className="sm:max-w-md" aria-describedby="ask-panel-description">
        <SheetTitle className="text-lg font-semibold">{title}</SheetTitle>
        <SheetDescription id="ask-panel-description" className="text-sm text-muted-foreground">
          {description}
        </SheetDescription>
        {body}
      </SheetContent>
    </Sheet>
  );
}
