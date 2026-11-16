/** Ownership shares are integer basis points (10000 = 100%). Integer maths only, never floats. */

export const FULL_SHARE_BP = 10000;

/** "60" -> 6000, "33.33" -> 3333, "60.5" -> 6050. Null if it is not a percentage with <= 2 decimals. */
export function percentToBp(text: string): number | null {
  const match = /^(\d{1,3})(?:\.(\d{1,2}))?$/.exec(text.trim());
  if (!match) return null;
  return Number(match[1]) * 100 + Number((match[2] ?? "").padEnd(2, "0"));
}

/** 6000 -> "60", 3333 -> "33.33", 5050 -> "50.5". */
export function bpToPercent(bp: number): string {
  const whole = Math.trunc(bp / 100);
  const frac = String(bp % 100)
    .padStart(2, "0")
    .replace(/0+$/, "");
  return frac ? `${whole}.${frac}` : String(whole);
}

export const formatShare = (bp: number) => `${bpToPercent(bp)}%`;

/** Even shares that sum to exactly 10000; the leftover units go to the first owners. */
export function evenShares(count: number): number[] {
  const base = Math.floor(FULL_SHARE_BP / count);
  const extra = FULL_SHARE_BP - base * count;
  return Array.from({ length: count }, (_, i) => base + (i < extra ? 1 : 0));
}
