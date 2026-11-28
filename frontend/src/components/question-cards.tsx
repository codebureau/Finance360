import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { MessageCircleQuestion } from "lucide-react";
import type { ReactNode } from "react";

import { useAsk } from "@/components/ask-panel";
import { GeneralError } from "@/components/form-fields";
import { StatTile } from "@/components/stat-tile";
import { Button } from "@/components/ui/button";
import { FROM_PARAM, linkTarget } from "@/lib/due";
import { fetchQuestions, fromQuestion, QUESTIONS_KEY, readiness, type Question } from "@/lib/questions";

/** A fix link as a router target that carries the return marker (`?from=q.<key>`). */
export function fixTarget(link: string, question: { key: string }) {
  const target = linkTarget(link);
  return { to: target.to, search: { ...target.search, [FROM_PARAM]: fromQuestion(question.key) } };
}

/** "Ask about this": opens Ask and asks the Question's fixed prompt at once (ADR-0032 section 4). */
export function AskAboutThis({ question }: { question: Pick<Question, "key" | "ask_prompt"> }) {
  const { askAbout } = useAsk();
  return (
    <Button
      variant="ghost"
      className="h-auto w-fit gap-1 p-0 text-sm font-normal underline hover:bg-transparent"
      onClick={() => askAbout(question.ask_prompt, `/questions/${question.key}`)}
    >
      <MessageCircleQuestion size={14} aria-hidden />
      Ask about this
    </Button>
  );
}

/**
 * A Question as a stat tile with severity (view keys `home.questions` and `question.page`, shape
 * `figure`, ADR-0032 and ADR-0031 section 6): the Question in plain words, its Readiness as a
 * status tone, an icon and a word, the count of inputs needing attention, one line of reason and
 * the single next step. The Home card and the Question page lead with the same tile; `children`
 * are the links that differ.
 */
export function QuestionTile({ question, children }: { question: Question; children: ReactNode }) {
  const { tone, icon: Icon } = readiness(question.state);
  const step = question.next_step;
  const target = step ? fixTarget(step.link, question) : null;
  return (
    <StatTile
      label={question.wording}
      hero={
        <>
          <span data-testid="question-count">{question.count}</span>
          <span className="ml-2 text-sm font-normal text-muted-foreground">
            {question.count === 1 ? "input needs attention" : "inputs need attention"}
          </span>
        </>
      }
      headline={question.reason}
      supporting={[]}
      severity={{ tone, icon: <Icon size={14} aria-hidden />, word: question.state_word }}
    >
      {step && target && <p className="text-sm text-muted-foreground">{step.effect}</p>}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
        {step && target && (
          <Link to={target.to} search={target.search} className="w-fit font-medium underline">
            Take the next step
          </Link>
        )}
        {children}
      </div>
    </StatTile>
  );
}

/** One Home card: the tile, with "Show items" to the Question's page. */
function QuestionCard({ question }: { question: Question }) {
  return (
    <QuestionTile question={question}>
      <Link to="/questions/$questionKey" params={{ questionKey: question.key }} className="w-fit underline">
        Show items
      </Link>
      <AskAboutThis question={question} />
    </QuestionTile>
  );
}

/**
 * Card 2 of Home (view key `home.questions`): a headline sentence over a card for each Question.
 * No gauge, no score and no percentage (ADR-0032 section 5): the sentence counts the states.
 */
export function QuestionCards() {
  // Coming back from fixing something shows the new state, not the one cached before.
  const query = useQuery({ queryKey: QUESTIONS_KEY, queryFn: fetchQuestions, refetchOnMount: "always" });
  const data = query.data;
  return (
    <section aria-labelledby="your-questions" className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 id="your-questions" className="text-xl font-semibold">
          Your questions
        </h2>
        {data && (
          <p className="text-sm text-muted-foreground" data-testid="questions-headline">
            {data.headline}
          </p>
        )}
      </div>
      {query.isPending && <p className="text-sm text-muted-foreground">Loading...</p>}
      <GeneralError error={query.error} />
      {data && (
        <div className="grid gap-3 sm:grid-cols-3">
          {data.questions.map((q) => (
            <QuestionCard key={q.key} question={q} />
          ))}
        </div>
      )}
    </section>
  );
}
