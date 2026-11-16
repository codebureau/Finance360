/**
 * The Guide's pages: Markdown files in `src/guide/`, bundled into the build as text so they ship
 * with each release. File names are `NN-slug.md`; `NN` is the order and the first `# ` line is the
 * title. The page named `start-here` is the Guide's front door at `/guide`.
 */
const FILES = import.meta.glob<string>("../guide/*.md", { query: "?raw", import: "default", eager: true });

export type GuidePage = { slug: string; title: string; body: string };

/** The page `/guide` opens on. */
export const START_SLUG = "start-here";

function parse(file: string, source: string): { order: number; page: GuidePage } {
  const name = /([^/]+)\.md$/.exec(file)?.[1] ?? file;
  const match = /^(\d+)-(.+)$/.exec(name);
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const at = lines.findIndex((l) => l.startsWith("# "));
  if (!match || at === -1) throw new Error(`Guide page ${file} needs an NN- prefix and a "# " title.`);
  const body = [...lines.slice(0, at), ...lines.slice(at + 1)].join("\n").trim();
  return { order: Number(match[1]), page: { slug: match[2], title: lines[at].slice(2).trim(), body } };
}

export const GUIDE_PAGES: GuidePage[] = Object.entries(FILES)
  .map(([file, source]) => parse(file, source))
  .sort((a, b) => a.order - b.order)
  .map((p) => p.page);

export function guidePath(slug: string): string {
  return slug === START_SLUG ? "/guide" : `/guide/${slug}`;
}

export function findGuidePage(slug: string | undefined): GuidePage | undefined {
  return GUIDE_PAGES.find((p) => p.slug === (slug ?? START_SLUG));
}

/** The Markdown links on a page, `[text](target)`: how the tests find every link to check. */
export function linksIn(markdown: string): { text: string; target: string }[] {
  return [...markdown.matchAll(/\[([^\]]+)\]\(([^)\s]+)\)/g)].map((m) => ({ text: m[1], target: m[2] }));
}

