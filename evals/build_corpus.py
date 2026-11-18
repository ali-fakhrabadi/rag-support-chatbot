"""Render the PDF documents of the evaluation corpus from their Markdown sources.

    python evals/build_corpus.py

Pages are separated by `<!-- page -->` in the source. Output is deterministic (reportlab's
invariant mode), so rebuilding does not change committed files.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import ListFlowable, ListItem, PageBreak, Paragraph, SimpleDocTemplate

CORPUS = Path(__file__).parent / "corpus"


def render(source: Path, target: Path) -> None:
    styles = getSampleStyleSheet()
    story: list[object] = []
    title = ""
    bullets: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            story.append(Paragraph(" ".join(paragraph), styles["BodyText"]))
            paragraph.clear()
        if bullets:
            items = [ListItem(Paragraph(b, styles["BodyText"])) for b in bullets]
            story.append(ListFlowable(items, bulletType="bullet"))
            bullets.clear()

    for raw in source.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line == "<!-- page -->":
            flush()
            story.append(PageBreak())
        elif line.startswith("# "):
            flush()
            title = line[2:]
            story.append(Paragraph(title, styles["Title"]))
        elif line.startswith("## "):
            flush()
            story.append(Paragraph(line[3:], styles["Heading2"]))
        elif line.startswith("- "):
            if paragraph:
                flush()
            bullets.append(line[2:])
        elif not line:
            flush()
        else:
            if bullets:  # continuation of the previous bullet
                bullets[-1] += " " + line
            else:
                paragraph.append(line)
    flush()
    doc = SimpleDocTemplate(
        str(target),
        pagesize=LETTER,
        title=title,
        author="Larkspur Outfitters (fictional)",
        invariant=1,
    )
    doc.build(story)


def main() -> None:
    for source in sorted(CORPUS.glob("*/pdf-src/*.md")):
        target = source.parent.parent / f"{source.stem}.pdf"
        render(source, target)
        print(f"wrote {target.relative_to(CORPUS)}")


if __name__ == "__main__":
    main()
