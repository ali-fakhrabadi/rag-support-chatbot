"""Render the branding images from the HTML templates with headless Chromium.

    python assets/branding/source/render.py \
        --results docs/evaluation/results-run2.json --fonts-dir /path/to/fonts

Numbers on the images are read from an evaluation results file, never typed by hand. Fonts
(Inter, JetBrains Mono; both SIL OFL 1.1) are not committed: pass a directory containing
inter-latin-wght-normal.woff2, jetbrains-mono-latin-400-normal.woff2 and
jetbrains-mono-latin-700-normal.woff2 (from the @fontsource npm packages). Screenshots are read
from assets/screenshots. Runs in the e2e image, which has Playwright and Chromium.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE.parent
IMAGES = {
    "07-rag-support-chatbot-cover.png": ("cover.html", 1600, 1200),
    "github-social-preview.png": ("social.html", 1280, 640),
    "upwork-catalog-hero.png": ("catalog-hero.html", 1600, 1200),
    "upwork-catalog-features.png": ("catalog-features.html", 1600, 1200),
    "upwork-catalog-architecture.png": ("architecture.html", 1600, 1200),
    "rag-architecture.png": ("architecture.html", 1600, 1200),
}


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def values(results: dict) -> dict[str, str]:
    m = results["metrics"]
    abstention = m["abstention"]
    return {
        "test_questions": str(m["questions"]),
        "recall_at_5": _pct(m["retrieval"]["recall_at_5"]),
        "abstention_accuracy": _pct(abstention["accuracy"]),
        "citation_accuracy": _pct(m["citations"]["citation_accuracy"]),
        "cross_tenant": str(m["security"]["cross_tenant_citations"]),
        "restricted": str(m["security"]["member_citations_of_admin_only_docs"]),
        "injection_passed": m["security"]["injection_passed"],
        "correct_abstentions": (
            f"{abstention['correct_abstentions']}/{abstention['should_abstain']}"
        ),
        "false_abstentions": f"{abstention['false_abstentions']}/{abstention['should_answer']}",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True)
    parser.add_argument("--fonts-dir", required=True)
    args = parser.parse_args()

    data = values(json.loads(Path(args.results).read_text(encoding="utf-8")))
    fonts = HERE / "fonts"
    shutil.rmtree(fonts, ignore_errors=True)
    shutil.copytree(args.fonts_dir, fonts)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for name, (template, width, height) in IMAGES.items():
                html = (HERE / template).read_text(encoding="utf-8")
                for key, value in {**data, "width": str(width), "height": str(height)}.items():
                    html = html.replace("{{" + key + "}}", value)
                assert "{{" not in html, f"unfilled placeholder in {template}"
                page_file = HERE / f".render-{template}"
                page_file.write_text(html, encoding="utf-8")
                page = browser.new_page(viewport={"width": width, "height": height})
                page.goto(page_file.as_uri())
                page.wait_for_load_state("networkidle")
                page.evaluate("document.fonts.ready")
                page.screenshot(path=str(OUT / name))
                page.close()
                page_file.unlink()
                print(f"rendered {name} ({width}x{height})")
            browser.close()
    finally:
        shutil.rmtree(fonts, ignore_errors=True)


if __name__ == "__main__":
    main()
