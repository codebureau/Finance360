// @vitest-environment node
import { ESLint } from "eslint";
import { expect, test } from "vitest";

// The ADR-0014 / docs/ui-conventions.md rule, exercised through the real eslint.config.js.
const eslint = new ESLint({ cwd: process.cwd() });

async function messages(code: string, filePath: string) {
  const [result] = await eslint.lintText(code, { filePath });
  return result.messages.map((m) => m.message);
}

const NEW_SCREEN = "src/components/new-screen.tsx";

test("a raw palette class is an error outside components/ui", async () => {
  const found = await messages('export const X = () => <p className="text-red-500">x</p>;', NEW_SCREEN);
  expect(found).toHaveLength(1);
  expect(found[0]).toMatch(/semantic token/);
});

test("hard-coded black and white, and hex colours, are errors too", async () => {
  expect(await messages('export const a = "bg-white p-3";', NEW_SCREEN)).toHaveLength(1);
  expect(await messages('export const b = `border-black ${1}`;', NEW_SCREEN)).toHaveLength(1);
  expect(await messages('export const c = "#ff0000";', NEW_SCREEN)).toHaveLength(1);
});

test("a bare <button> is an error outside components/ui", async () => {
  const found = await messages("export const X = () => <button>x</button>;", NEW_SCREEN);
  expect(found).toHaveLength(1);
  expect(found[0]).toMatch(/<Button>/);
});

test("semantic tokens are fine", async () => {
  const code = 'export const X = () => <p className="bg-primary text-muted-foreground bg-warning/10">x</p>;';
  expect(await messages(code, NEW_SCREEN)).toEqual([]);
});

test("components/ui may use a bare <button>", async () => {
  expect(await messages("export const X = () => <button>x</button>;", "src/components/ui/thing.tsx")).toEqual([]);
});

test("every screen is linted, Position included; only the chart canvas files may hold literal colours", async () => {
  const bare = "export const X = () => <button>x</button>;";
  expect(await messages(bare, "src/components/position-page.tsx")).toHaveLength(1);
  expect(await messages(bare, "src/components/position-history.tsx")).toHaveLength(1);
  const hex = 'export const C = "#2f855a";';
  expect(await messages(hex, "src/lib/history-chart.ts")).toEqual([]);
  expect(await messages(hex, "src/lib/position.ts")).toHaveLength(1);
});
