import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { AskAboutThis, fixTarget, QuestionTile } from "@/components/question-cards";
import { GeneralError } from "@/components/form-fields";
import { SEVERITY_BADGE } from "@/components/stat-tile";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { ApiError } from "@/lib/household";
import {
  fetchQuestion,
  questionKey,
  readiness,
  type QuestionDetail,
  type QuestionInputView,
} from "@/lib/questions";

/** A Readiness as a badge: its icon, its word and its tone, so colour is never the only cue. */
function ReadinessBadge({ state, word }: { state: string; word: string }) {
  const { tone, icon: Icon } = readiness(state);
  return (
    <Badge variant={SEVERITY_BADGE[tone]} className="w-fit items-center gap-1">
      <Icon size={14} aria-hidden />
      {word}
    </Badge>
  );
}

function InputRow({ n, input, question }: { n: number; input: QuestionInputView; question: QuestionDetail }) {
  const needs = input.reasons.filter((r) => r.material);
  const small = input.reasons.filter((r) => !r.material);
  return (
    <li className="flex flex-col gap-2 py-3" data-testid="question-input" data-input={input.key}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-medium">
          {n}. {input.name}
        </span>
        <ReadinessBadge state={input.state} word={input.state_word} />
        {input.importance === "optional" && <Badge variant="closed">Optional</Badge>}
        {!input.asked && <span className="text-xs text-muted-foreground">Not asked at your Effort level</span>}
      </div>
      {needs.length === 0 && small.length === 0 && (
        <p className="text-sm text-muted-foreground">
          {input.state === "ready" ? "Nothing to do here." : "Nothing to do here yet."}
        </p>
      )}
      {needs.length > 0 && (
        <ul className="flex flex-col gap-2">
          {needs.map((r) => {
            const target = fixTarget(r.link, question);
            return (
              <li key={`${r.kind}:${r.link}:${r.title}`} className="flex flex-col gap-0.5 text-sm">
                <span className="break-words">{r.title}</span>
                <span className="text-muted-foreground">{r.effect}</span>
                <Link to={target.to} search={target.search} className="w-fit font-medium underline">
                  Fix it here
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      {small.length > 0 && (
        <div className="flex flex-col gap-1 text-sm text-muted-foreground" data-testid="small-gaps">
          <span className="text-xs font-medium tracking-wide uppercase">Small gaps</span>
          <ul className="flex flex-col gap-1">
            {small.map((r) => (
              <li key={`${r.kind}:${r.link}:${r.title}`} className="break-words">
                {r.title}{" "}
                <Link to={fixTarget(r.link, question).to} search={fixTarget(r.link, question).search} className="underline">
                  Open
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

/**
 * One Question (`/questions/$questionKey`, view key `question.page`, ADR-0032 section 3): the same
 * tile as its Home card leads (status, reason, next step), then the inputs in the order to provide
 * them, each with its status and a link to fix it. The fix links carry `?from=q.<key>`, so the
 * return bar brings the owner back here, recalculated.
 */
export function QuestionPage({ questionKey: key }: { questionKey: string }) {
  // Coming back from a fix shows the new state, not the one cached before.
  const query = useQuery({
    queryKey: questionKey(key),
    queryFn: () => fetchQuestion(key),
    refetchOnMount: "always",
    retry: false,
  });
  const q = query.data;
  if (query.error instanceof ApiError && query.error.status === 404) {
    return (
      <div className="flex max-w-3xl flex-col gap-3" role="alert">
        <PageHeader title="Question not found" description="There is no question at this address." />
        <Link to="/" className="w-fit underline">
          Back to Home
        </Link>
      </div>
    );
  }
  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <PageHeader title={q?.wording ?? "Question"} description="Your questions" />
      {query.isPending && <p className="text-sm text-muted-foreground">Loading...</p>}
      <GeneralError error={query.error} />
      {q && (
        <>
          <QuestionTile question={q}>
            <Link to={q.screen} className="w-fit underline">
              See the answer
            </Link>
            <AskAboutThis question={q} />
          </QuestionTile>
          <Card>
            <section aria-labelledby="question-inputs" className="flex flex-col gap-1 p-4">
              <h2 id="question-inputs" className="text-base font-semibold">
                What this answer needs
              </h2>
              <p className="text-sm text-muted-foreground">In the order that helps most.</p>
              <ol className="flex flex-col divide-y divide-border" aria-label="Inputs">
                {q.inputs.map((input, i) => (
                  <InputRow key={input.key} n={i + 1} input={input} question={q} />
                ))}
              </ol>
            </section>
          </Card>
        </>
      )}
    </div>
  );
}
