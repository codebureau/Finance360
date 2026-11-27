import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { GeneralError } from "@/components/form-fields";
import { StatTile } from "@/components/stat-tile";
import { linkTarget } from "@/lib/due";
import { fetchQuestions, QUESTIONS_KEY, readiness, type Question } from "@/lib/questions";

/**
 * One Question card (view key `home.questions`, shape `figure` with severity, ADR-0032 and
 * ADR-0031 section 6): the Question in plain words, its Readiness as a status tone, an icon and a
 * word, the count of inputs needing attention, one line of reason and the single next step. "Show
 * items" goes to the screen that answers the Question until the Question page arrives (#121).
 */
function QuestionCard({ question }: { question: Question }) {
  const { tone, icon: Icon } = readiness(question.state);
  const step = question.next_step;
  const target = step ? linkTarget(step.link) : null;
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
      {step && target && (
        <p className="text-sm text-muted-foreground">{step.effect}</p>
      )}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
        {step && target && (
          <Link to={target.to} search={target.search} className="w-fit font-medium underline">
            Take the next step
          </Link>
        )}
        <Link to={question.screen} className="w-fit underline">
          Show items
        </Link>
      </div>
    </StatTile>
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
