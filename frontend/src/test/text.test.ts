import { describe, expect, it } from "vitest";
import { unwrapLines } from "../text";

describe("unwrapLines", () => {
  it("joins hard-wrapped lines but keeps paragraphs", () => {
    expect(unwrapLines("Most items can be returned\nwithin 45 days.\n\nTags must be\nattached.")).toBe(
      "Most items can be returned within 45 days.\n\nTags must be attached.",
    );
  });

  it("keeps headings, list items and table rows on their own lines", () => {
    const text = "## First day\nBadge at 9 a.m.\n- laptop\n- badge\n| a | b |\n| 1 | 2 |";
    expect(unwrapLines(text)).toBe(
      "## First day\nBadge at 9 a.m.\n- laptop\n- badge\n| a | b |\n| 1 | 2 |",
    );
  });

  it("keeps short heading lines of PDF text apart from the paragraph", () => {
    const text =
      "Repairs\nRepairs are done at the Larkspur Repair Center in Ogden, Utah. Turnaround is\n" +
      "usually 3 to 4 weeks, plus shipping time. Larkspur pays shipping both ways for\n" +
      "approved repairs.";
    expect(unwrapLines(text)).toBe(
      "Repairs\nRepairs are done at the Larkspur Repair Center in Ogden, Utah. Turnaround is " +
        "usually 3 to 4 weeks, plus shipping time. Larkspur pays shipping both ways for " +
        "approved repairs.",
    );
  });

  it("starts a heading that follows a full line on its own line", () => {
    const text =
      "If the claim is approved, Larkspur will repair or replace the item for you. Larkspur\n" +
      "chooses the remedy and pays the shipping both ways for every approved claim.\n" +
      "Repairs\n" +
      "Repairs are done at the Larkspur Repair Center in Ogden, Utah, within four weeks.";
    expect(unwrapLines(text)).toBe(
      "If the claim is approved, Larkspur will repair or replace the item for you. Larkspur " +
        "chooses the remedy and pays the shipping both ways for every approved claim.\n" +
        "Repairs\n" +
        "Repairs are done at the Larkspur Repair Center in Ogden, Utah, within four weeks.",
    );
  });

  it("joins the continuation of a wrapped list item", () => {
    expect(unwrapLines("1. Report the email\n   with the button.\n2. Delete it.")).toBe(
      "1. Report the email with the button.\n2. Delete it.",
    );
  });
});
