import type { ScenarioProjection } from "@/lib/scenarios";

type Used = NonNullable<ScenarioProjection["age_pension"]>;
type Working = NonNullable<ScenarioProjection["rows"][number]["age_pension"]>;

// Invented figures only. Alex is eligible from year 2 (index 1); Jordan later.
export const agePensionUsed = (over: Partial<Used> = {}): Used => ({
  status: "calculated",
  reasons: [],
  facts: [
    { key: "couple_status", value: "couple", source: "entered", label: "entered" },
    { key: "homeowner", value: "yes", source: "default", label: "default, not entered" },
    { key: "principal_home", value: "5", source: "entered", label: "entered" },
  ],
  eligibility: [
    { member_id: 1, on: "2027-03-12", year: 1 },
    { member_id: 2, on: "2030-09-30", year: 4 },
  ],
  rules: [
    {
      key: "age_pension.max_basic.couple_each",
      from_year: 0,
      financial_year: "2026-27",
      value: "900.00",
      origin: "shipped",
      capture_id: null,
      provisional: false,
      provisional_note: null,
    },
    {
      key: "age_pension.assets_taper",
      from_year: 0,
      financial_year: "2026-27",
      value: "3",
      origin: "captured",
      capture_id: 4,
      provisional: true,
      provisional_note: "Announced, not yet law (invented).",
    },
    {
      key: "age_pension.assets_taper",
      from_year: 5,
      financial_year: "2027-28",
      value: "3.5",
      origin: "shipped",
      capture_id: null,
      provisional: false,
      provisional_note: null,
    },
  ],
  assets: [
    { account_id: 5, name: "Family Home", category_key: "property", role: "exempt_home", value: "800000.00", valued_on: "2026-09-26" },
    { account_id: 6, name: "Family Car", category_key: "vehicles", role: "assessed", value: "20000.00", valued_on: "2026-06-23" },
  ],
  left_out: [{ account_id: 9, name: "Old shares", category_key: "investments", status: "no_valuation" }],
  simplifications: [{ key: "no_debt_deduction", text: "No deduction for debt (invented wording)." }],
  ...over,
});

export const agePensionYear = (over: Partial<Working> = {}): Working => ({
  paid: [{ member_id: 1, amount: "9000.50" }],
  couple: true,
  homeowner: true,
  categories: [
    { category_key: "cash", amount: "300000.00" },
    { category_key: "investments", amount: "100000.00" },
  ],
  property_assets: "0.00",
  vehicle_assets: "20000.00",
  assets: "420000.00",
  financial_assets: "400000.00",
  other_income: "1000.00",
  deemed_income: "15000.00",
  maximum: "36000.00",
  income_test: "20001.00",
  assets_test: "18001.00",
  bound: "assets",
  total: "18001.00",
  ...over,
});
