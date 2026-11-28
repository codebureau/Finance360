import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { FieldMessage, GeneralError, inputClass } from "@/components/form-fields";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import {
  ANSWERS,
  answerIcon,
  fetchRatings,
  QUESTIONS_KEY,
  rateQuestion,
  ratedOn,
  RATINGS_KEY,
  ratingSentence,
  ratingsText,
  type Answer,
  type QuestionDetail,
  type Rating,
} from "@/lib/questions";
import { cn } from "@/lib/utils";

const NOTE_MAX = 200;

/** The rating as an icon and its word: an opinion, so no status colour (a No is never red). */
export function AnswerWord({ answer, word, className }: { answer: string; word: string; className?: string }) {
  const Icon = answerIcon(answer);
  return (
    <span className={cn("inline-flex items-center gap-1", className)}>
      <Icon size={14} aria-hidden />
      {word}
    </span>
  );
}

function EarlierRatings({ ratings }: { ratings: Rating[] }) {
  if (ratings.length === 0) return null;
  return (
    <details className="text-sm" data-testid="earlier-ratings">
      <summary className="w-fit cursor-pointer underline">Earlier ratings ({ratings.length})</summary>
      <ul className="mt-2 flex flex-col gap-2 text-muted-foreground">
        {ratings.map((r) => (
          <li key={r.created_at} className="break-words">
            {ratingSentence(r)}
            {r.note && <span className="block">&ldquo;{r.note}&rdquo;</span>}
          </li>
        ))}
      </ul>
    </details>
  );
}

/**
 * "Did this answer your question?" on the Question page (ADR-0032 section 6): Yes, Partly or No, an
 * optional one-line note, and the current rating as a muted line beside (never inside) the
 * Readiness state. Nothing asks for a rating: acting is the opt-in. No score, anywhere.
 */
export function RatingCard({ question }: { question: QuestionDetail }) {
  const queryClient = useQueryClient();
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [note, setNote] = useState("");
  const save = useMutation({
    mutationFn: () => rateQuestion(question.key, answer as Answer, note),
    onSuccess: async () => {
      setAnswer(null);
      setNote("");
      await queryClient.invalidateQueries({ queryKey: QUESTIONS_KEY });
    },
  });
  const current = question.rating;
  return (
    <Card>
      <section aria-labelledby="question-rating" className="flex flex-col gap-3 p-4">
        <h2 id="question-rating" className="text-base font-semibold">
          Did this answer your question?
        </h2>
        <p className="text-sm text-muted-foreground" data-testid="current-rating">
          {current ? ratingSentence(current) : "Not rated"}
        </p>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (answer) save.mutate();
          }}
        >
          <div role="group" aria-label="Your answer" className="flex flex-wrap gap-2">
            {ANSWERS.map((a) => (
              <Button
                key={a.key}
                variant={answer === a.key ? "default" : "outline"}
                aria-pressed={answer === a.key}
                onClick={() => setAnswer(a.key)}
              >
                <a.icon size={16} aria-hidden />
                {a.word}
              </Button>
            ))}
          </div>
          <label className="flex flex-col gap-1 text-sm">
            What were you hoping to see? (optional)
            <input
              className={inputClass}
              value={note}
              maxLength={NOTE_MAX}
              onChange={(e) => setNote(e.target.value)}
              aria-describedby="rating-note-hint"
            />
            <span id="rating-note-hint" className="text-xs text-muted-foreground">
              Please do not type amounts or account numbers.
            </span>
            <FieldMessage error={save.error} field="note" />
          </label>
          <GeneralError error={save.error} />
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <Button type="submit" disabled={!answer || save.isPending}>
              Save rating
            </Button>
            <Link to="/questions/ratings" className="text-sm underline">
              See all your ratings
            </Link>
          </div>
        </form>
        <EarlierRatings ratings={question.earlier_ratings} />
      </section>
    </Card>
  );
}

/**
 * `/questions/ratings` (view key `questions.ratings`, a plain text list: no chart, there is no
 * series and no score): every Question with its current rating, note and the Readiness state at the
 * time, as text the owner can copy.
 */
export function RatingsPage() {
  const query = useQuery({ queryKey: RATINGS_KEY, queryFn: fetchRatings, refetchOnMount: "always" });
  const rows = query.data;
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    if (!rows) return;
    try {
      await navigator.clipboard.writeText(ratingsText(rows));
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <PageHeader title="Your ratings" description="Whether each answer helped, in your words. Kept on this instance only." />
      {query.isPending && <p className="text-sm text-muted-foreground">Loading...</p>}
      <GeneralError error={query.error} />
      {rows && (
        <Card>
          <section aria-label="Ratings" className="flex flex-col gap-3 p-4">
            <ul className="flex flex-col divide-y divide-border">
              {rows.map((row) => (
                <li key={row.key} className="flex flex-col gap-1 py-3 text-sm" data-testid="rating-row" data-question={row.key}>
                  <Link to="/questions/$questionKey" params={{ questionKey: row.key }} className="w-fit font-medium underline">
                    {row.wording}
                  </Link>
                  {row.rating ? (
                    <>
                      <p>
                        <AnswerWord answer={row.rating.answer} word={row.rating.answer_word} className="font-medium" />
                        <span className="text-muted-foreground">
                          {" "}
                          on {ratedOn(row.rating)}, when it was {row.rating.readiness_word}
                        </span>
                      </p>
                      {row.rating.note && <p className="break-words text-muted-foreground">&ldquo;{row.rating.note}&rdquo;</p>}
                    </>
                  ) : (
                    <p className="text-muted-foreground">Not rated</p>
                  )}
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap items-center gap-3">
              <Button variant="outline" onClick={() => void copy()}>
                Copy
              </Button>
              {copied && (
                <span role="status" className="text-sm text-muted-foreground">
                  Copied
                </span>
              )}
            </div>
          </section>
        </Card>
      )}
    </div>
  );
}
