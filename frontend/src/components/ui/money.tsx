import { formatMoney } from "@/lib/money";
import { cn } from "@/lib/utils";

/**
 * A money amount. Negative amounts and liabilities take the `money-negative` token and always
 * carry a leading minus sign, so colour is never the only cue. The amount stays an exact decimal
 * string; `null` renders as a dash.
 */
export function Money({
  amount,
  currency = "AUD",
  liability = false,
  className,
}: {
  amount: string | null | undefined;
  currency?: string;
  /** A liability balance is shown as negative even when the API sends it unsigned. */
  liability?: boolean;
  className?: string;
}) {
  const known = amount !== null && amount !== undefined;
  const isZero = known && /^-?0+(\.0+)?$/.test(amount);
  const signed = known && !amount.startsWith("-") && liability && !isZero ? `-${amount}` : amount;
  const negative = known && !isZero && (signed as string).startsWith("-");
  return (
    <span
      data-negative={negative || undefined}
      className={cn("font-mono tabular-nums", negative && "text-money-negative", className)}
    >
      {formatMoney(signed, currency)}
    </span>
  );
}
