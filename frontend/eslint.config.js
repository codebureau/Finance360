import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

// docs/ui-conventions.md, ADR-0014: semantic tokens only (no raw palette classes or hard-coded
// black/white) and no bare <button> outside components/ui/ (use <Button>).
const PALETTE = "red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|slate|gray|zinc|neutral|stone";
const UTILITY = "bg|text|border|ring|fill|stroke|from|to|via|divide|outline|decoration|shadow|accent|caret|placeholder";
const RAW_CLASS = `\\b(${UTILITY})-((${PALETTE})-[0-9]{2,3}|white|black)\\b|#[0-9a-fA-F]{3,8}\\b`;
const rawColourClass = {
  message: "Use a semantic token (bg-primary, text-muted-foreground, ...), not a raw colour. See docs/ui-conventions.md.",
};
const UI_RULES = {
  "no-restricted-syntax": [
    "error",
    {
      selector: "JSXOpeningElement[name.name='button']",
      message: "Use <Button> from components/ui/button, not a bare <button>. See docs/ui-conventions.md.",
    },
    { selector: `Literal[value=/${RAW_CLASS}/]`, ...rawColourClass },
    { selector: `TemplateElement[value.raw=/${RAW_CLASS}/]`, ...rawColourClass },
  ],
};

// The ECharts canvas cannot read CSS variables, so the chart option carries a literal fallback
// palette (used only where the tokens cannot be resolved, e.g. no canvas). Everything else is
// token-only; this is the only exemption.
const CANVAS_COLOURS = ["src/lib/history-chart.ts", "src/lib/chart-palette.ts"];

export default tseslint.config(
  { ignores: ["dist", "src/api/schema.d.ts"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: { globals: globals.browser },
    plugins: { "react-hooks": reactHooks },
    rules: reactHooks.configs.recommended.rules,
  },
  {
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/components/ui/**", "src/**/*.test.{ts,tsx}", ...CANVAS_COLOURS],
    rules: UI_RULES,
  },
);
