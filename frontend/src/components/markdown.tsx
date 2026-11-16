import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

/**
 * A deliberately small Markdown renderer for the Guide's own pages: `##` and `###` headings,
 * paragraphs, `-` and `1.` lists, `>` callouts, `**bold**`, `*italic*`, `` `code` `` and
 * `[links](/app/path)`. Links starting with `/` are in-app (router) links; `http(s)` links open in
 * a new tab. A line `:::name` places the component `blocks[name]`. Nothing else is supported, on
 * purpose: the pages are written for it.
 */
type Block =
  | { t: "h"; level: 2 | 3; text: string }
  | { t: "p"; text: string }
  | { t: "ul" | "ol"; items: string[] }
  | { t: "quote"; text: string }
  | { t: "block"; name: string };

export function parseBlocks(source: string): Block[] {
  const blocks: Block[] = [];
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === "") {
      i++;
    } else if (/^###? /.test(line)) {
      const level = line.startsWith("### ") ? 3 : 2;
      blocks.push({ t: "h", level, text: line.slice(level + 1).trim() });
      i++;
    } else if (/^:::\S+$/.test(line.trim())) {
      blocks.push({ t: "block", name: line.trim().slice(3) });
      i++;
    } else if (/^(- |\d+\. )/.test(line)) {
      const ordered = /^\d+\. /.test(line);
      const items: string[] = [];
      while (i < lines.length && lines[i].trim() !== "") {
        const marker = /^(- |\d+\. )/.exec(lines[i]);
        if (marker) items.push(lines[i].slice(marker[0].length).trim());
        else items[items.length - 1] += ` ${lines[i].trim()}`;
        i++;
      }
      blocks.push({ t: ordered ? "ol" : "ul", items });
    } else if (line.startsWith(">")) {
      const quoted: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) quoted.push(lines[i++].replace(/^>\s?/, ""));
      blocks.push({ t: "quote", text: quoted.join(" ").trim() });
    } else {
      const para: string[] = [];
      while (i < lines.length && lines[i].trim() !== "" && !/^(#|- |\d+\. |>|:::)/.test(lines[i])) {
        para.push(lines[i++].trim());
      }
      blocks.push({ t: "p", text: para.join(" ") });
    }
  }
  return blocks;
}

const INLINE = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\))/;
const LINK_CLASS = "text-primary underline underline-offset-4 hover:no-underline";

function Inline({ text }: { text: string }): ReactNode {
  return text.split(INLINE).map((part, n) => {
    if (part.startsWith("**")) return <strong key={n}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`")) {
      return (
        <code key={n} className="rounded bg-muted px-1 py-0.5 font-mono text-xs break-words">
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("[")) {
      const m = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(part);
      if (m) {
        const [, label, target] = m;
        return target.startsWith("/") ? (
          <Link key={n} to={target} className={LINK_CLASS}>
            <Inline text={label} />
          </Link>
        ) : (
          <a key={n} href={target} target="_blank" rel="noreferrer" className={LINK_CLASS}>
            {label}
          </a>
        );
      }
    }
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) return <em key={n}>{part.slice(1, -1)}</em>;
    return part;
  });
}

export function Markdown({ source, blocks = {} }: { source: string; blocks?: Record<string, ReactNode> }) {
  return (
    <div className="flex flex-col gap-3 text-sm leading-6">
      {parseBlocks(source).map((b, n) => {
        switch (b.t) {
          case "h":
            return b.level === 2 ? (
              <h2 key={n} className="mt-4 text-lg font-semibold">
                <Inline text={b.text} />
              </h2>
            ) : (
              <h3 key={n} className="mt-2 text-base font-medium">
                <Inline text={b.text} />
              </h3>
            );
          case "p":
            return (
              <p key={n}>
                <Inline text={b.text} />
              </p>
            );
          case "ul":
          case "ol": {
            const List = b.t;
            return (
              <List key={n} className={`${b.t === "ul" ? "list-disc" : "list-decimal"} flex flex-col gap-1.5 pl-5`}>
                {b.items.map((item, k) => (
                  <li key={k}>
                    <Inline text={item} />
                  </li>
                ))}
              </List>
            );
          }
          case "quote":
            return (
              <blockquote key={n} className="rounded-md border-l-4 border-primary/40 bg-muted/50 px-3 py-2">
                <Inline text={b.text} />
              </blockquote>
            );
          case "block":
            return <div key={n}>{blocks[b.name] ?? null}</div>;
        }
      })}
    </div>
  );
}
