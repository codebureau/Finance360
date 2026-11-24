import { expect, test } from "vitest";

import type { ExposureDimension, ExposureProfile, LookThrough } from "@/lib/exposure";
import { buildBarOption, foldRows, type ShareRow } from "@/lib/charts/share";
import {
  headline,
  labelTone,
  lookThroughRows,
  profileHeadline,
  profileRows,
  referenceRows,
  totalBarGeometry,
} from "@/lib/exposure-chart";

const aud = (amount: string, minor: number) => ({ amount, amount_minor: minor, currency: "AUD" });
const vocab = [
  { key: "asset_class", name: "Asset class", labels: [] },
  {
    key: "region",
    name: "Region",
    labels: [
      { key: "australia", name: "Australia" },
      { key: "japan", name: "Japan" },
      { key: "other", name: "Other" },
    ],
  },
  {
    key: "exposure_currency",
    name: "Exposure currency",
    labels: ["aud", "usd", "eur", "gbp", "jpy", "cad", "chf", "other"].map((k) => ({ key: k, name: k.toUpperCase() })),
  },
] as ExposureDimension[];

const line = (kind: string, key: string | null, name: string, percent: string) =>
  ({ kind, key, name, percent, amount: aud("1.00", 100), instrument_id: null }) as LookThrough["lines"][number];
const look = (lines: LookThrough["lines"], over: Partial<LookThrough> = {}) =>
  ({ dimension_key: "asset_class", dimension_name: "Asset class", flagged: false, lines, ...over }) as LookThrough;

test("a label keeps the same colour token in different views (never by rank or position)", () => {
  expect(labelTone("exposure_currency", "usd", vocab)).toEqual({ token: 1 });
  const cur = { dimension_key: "exposure_currency" };
  const a = lookThroughRows(look([line("label", "usd", "USD", "90"), line("label", "eur", "EUR", "10")], cur), vocab);
  const b = lookThroughRows(look([line("label", "eur", "EUR", "70"), line("label", "usd", "USD", "30")], cur), vocab);
  const toneOf = (rows: ShareRow[], name: string) => rows.find((r) => r.name === name)?.tone;
  expect(toneOf(a, "USD")).toEqual(toneOf(b, "USD"));
  expect(toneOf(a, "EUR")).toEqual(toneOf(b, "EUR"));
  // asset class keeps its fixed tokens; other and unknown are muted
  expect(labelTone("asset_class", "cash", vocab)).toEqual({ token: 2 }); // teal, never red
  expect(labelTone("asset_class", "alternatives", vocab)).toBe("muted");
  expect(labelTone("region", "other", vocab)).toBe("muted");
});

test("more than six bars fold the smallest into one muted Other, but explicit lines stay", () => {
  const labels = ["aud", "usd", "eur", "gbp", "jpy", "cad", "chf"].map((k, i) => ({
    key: k,
    name: k.toUpperCase(),
    ppm: (70 - i * 10) * 10_000 + 5_000,
    percentText: "x",
    amountText: null,
    tone: labelTone("exposure_currency", k, vocab),
  }));
  const noProfile: ShareRow = { key: "no_profile", name: "No profile: Sample ETF", ppm: 20_000, percentText: "2%", amountText: null, tone: "warning" };
  const rows = foldRows(labels, [noProfile]);
  expect(rows).toHaveLength(6);
  expect(rows.at(-1)).toBe(noProfile);
  const other = rows.find((r) => r.name === "Other")!;
  expect(other.tone).toBe("muted");
  // the folded labels are the smallest three of seven; the sum is exact integer ppm
  expect(other.ppm).toBe(labels[4].ppm + labels[5].ppm + labels[6].ppm);
  expect(other.percentText).toBe("61.5%");
  expect(rows.map((r) => r.name)).toEqual(["AUD", "USD", "EUR", "GBP", "Other", "No profile: Sample ETF"]);
});

test("a view with few labels is not folded, and a zero line is left out of the picture", () => {
  const rows = lookThroughRows(
    look([
      line("label", "australian_shares", "Australian shares", "60.00"),
      line("label", "cash", "Cash", "40.00"),
      line("unallocated", null, "Unallocated", "0.00"),
    ]),
    vocab,
  );
  expect(rows.map((r) => r.name)).toEqual(["Australian shares", "Cash"]);
});

test("the headline names the top labels and what is missing", () => {
  const data = look([
    line("label", "international_shares", "International shares", "28.56"),
    line("label", "australian_shares", "Australian shares", "49.10"),
    line("no_profile", null, "No profile: Sample ETF", "9.41"),
    line("not_classified", null, "Not classified", "0.36"),
  ]);
  expect(headline(data)).toBe("Mostly Australian shares and International shares; 9.41% has no profile yet");
  const gappy = look(
    [line("label", "cash", "Cash", "20"), line("not_classified", null, "Not classified", "70"), line("unallocated", null, "Unallocated", "10")],
    { flagged: true },
  );
  expect(headline(gappy)).toBe("Largest: Cash; 70% is not classified; 10% is unallocated, beyond tolerance");
  // a stale super option mix beyond tolerance says the mix may have changed
  const stale = look(
    [line("label", "cash", "Cash", "80"), line("unallocated", null, "Unallocated", "20")],
    { flagged: true, change_since: "2026-06-30" },
  );
  expect(headline(stale)).toBe("Mostly Cash; 20% is unallocated: the option mix may have changed");
  const none = look([line("not_classified", null, "Not classified", "100")], { dimension_name: "Sector" });
  expect(headline(none)).toBe("Nothing is classified by sector yet");
});

test("profile rows chart one Dimension only", () => {
  const weights = [
    { dimension_key: "region", label_key: "japan", percent: "30" },
    { dimension_key: "region", label_key: "australia", percent: "70" },
    { dimension_key: "asset_class", label_key: "cash", percent: "100" },
  ];
  expect(profileRows(weights, vocab[1], vocab).map((r) => r.name)).toEqual(["Australia", "Japan"]);
  expect(profileRows(weights, vocab[2], vocab)).toEqual([]);
});

test("the bar option plots one bar per row in the palette's colours, with exact text in the tooltip", () => {
  const rows = lookThroughRows(
    look([line("label", "cash", "Cash", "60.00"), line("no_profile", null, "No profile: A <b>", "40.00")]),
    vocab,
  );
  const pal = { text: "#000", muted: "#777", grid: "#ddd", card: "#fff", lines: ["#1", "#2", "#3", "#4", "#5", "#6"], warning: "#w" };
  const opt = buildBarOption(rows, pal);
  expect(opt.series[0].data.map((d) => d.itemStyle.color)).toEqual(["#3", "#w"]);
  expect(opt.series[0].data.map((d) => d.value)).toEqual([60, 40]);
  const html = opt.tooltip.formatter({ dataIndex: 1 });
  expect(html).toContain("40.00%");
  expect(html).toContain("1.00");
  expect(html).not.toContain("<b>");
});

test("the total bar geometry puts the accepted band around 100%", () => {
  const g = totalBarGeometry(1_000_000);
  expect(g.bandStart).toBeLessThan(g.fill);
  expect(g.bandEnd).toBeGreaterThan(g.fill);
  expect(totalBarGeometry(0).fill).toBe(0);
  expect(totalBarGeometry(5_000_000).fill).toBe(100);
});

const fundProfile = (percents: string[]) =>
  ({
    weights: [{ dimension_key: "asset_class", label_key: "cash", percent: "10" }],
    references: percents.map((percent, i) => ({ instrument_id: i + 2, instrument_name: `Fund ${i + 1}`, percent })),
  }) as unknown as ExposureProfile;

test("a fund of funds' shares are each underlying fund and what it holds directly, summed in ppm", () => {
  const rows = referenceRows(fundProfile(["55", "35"]));
  expect(rows.map((r) => [r.name, r.ppm, r.percentText])).toEqual([
    ["Fund 1", 550_000, "55%"],
    ["Fund 2", 350_000, "35%"],
    ["Held directly", 100_000, "10%"],
  ]);
  expect(rows.reduce((sum, r) => sum + r.ppm, 0)).toBe(1_000_000);
  // a fund keeps its colour by its order in the profile, and no two share one
  const tokens = rows.map((r) => (typeof r.tone === "object" ? r.tone.token : -1));
  expect(new Set(tokens).size).toBe(3);
  // nothing held directly when the funds cover all of it
  expect(referenceRows(fundProfile(["60", "40"])).map((r) => r.name)).toEqual(["Fund 1", "Fund 2"]);
  expect(referenceRows(fundProfile([])).length).toBe(0);
});

test("the profile headline leads with the funds held, or with the largest asset classes", () => {
  expect(profileHeadline(fundProfile(["35", "55", "5"]), vocab)).toBe(
    "A fund of funds: 95% is held in Fund 2 (55%) and Fund 1 (35%) and 1 more",
  );
  const plain = {
    references: [],
    weights: [
      { dimension_key: "asset_class", label_key: "cash", percent: "10" },
      { dimension_key: "asset_class", label_key: "other", percent: "90" },
    ],
  } as unknown as ExposureProfile;
  const withLabels = [{ key: "asset_class", name: "Asset class", labels: [{ key: "cash", name: "Cash" }, { key: "other", name: "Other" }] }] as ExposureDimension[];
  expect(profileHeadline(plain, withLabels)).toBe("Mostly Other and Cash");
  expect(profileHeadline({ references: [], weights: [] } as unknown as ExposureProfile, withLabels)).toBe(
    "No asset class weights are published",
  );
});

test("a fund-of-funds' own holdings chart beside the share held through its underlying funds", () => {
  const weights = [{ dimension_key: "region", label_key: "australia", percent: "10" }];
  const rows = profileRows(weights, vocab[1], vocab, 900_000);
  expect(rows.map((r) => [r.name, r.percentText, r.tone])).toEqual([
    ["Australia", "10%", expect.anything()],
    ["Through underlying funds", "90%", "muted"],
  ]);
  expect(rows.reduce((s, r) => s + r.ppm, 0)).toBe(1_000_000);
  // A Dimension the fund leaves to its underlying funds stays empty, not a ring of one muted part.
  expect(profileRows(weights, vocab[2], vocab, 900_000)).toEqual([]);
});
