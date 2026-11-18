// Source files and PDF text are often hard-wrapped at a fixed width. For display, join the
// wrapped lines of each paragraph, but keep paragraph breaks, Markdown headings, list items,
// table rows, and short lines such as PDF headings (a wrapped line is close to the full width).
const BLOCK_START = /^([-*+•]\s|\d+[.)]\s|#|\|)/;

export function unwrapLines(text: string): string {
  return text
    .split(/\n[ \t]*\n/)
    .map((paragraph) => {
      const lines = paragraph.split("\n").map((line) => line.trim());
      const fullWidth = Math.max(...lines.map((line) => line.length));
      return lines.reduce((out, line, i) => {
        if (i === 0) return line;
        const previous = lines[i - 1] ?? "";
        const short = (s: string) => s.length < 0.6 * fullWidth;
        // A short line without closing punctuation after a finished sentence is a heading.
        const heading =
          i < lines.length - 1 && short(line) && /[.!?:]$/.test(previous) && !/[.!?:;,)"]$/.test(line);
        const keepBreak =
          BLOCK_START.test(line) || previous.startsWith("#") || short(previous) || heading;
        return `${out}${keepBreak ? "\n" : " "}${line}`;
      }, "");
    })
    .join("\n\n")
    .trim();
}
