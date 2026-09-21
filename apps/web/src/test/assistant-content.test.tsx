import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AssistantContent } from "../assistant-content";

describe("assistant incident tables", () => {
  it("renders multiple pipe tables with surrounding text, CRLF and escaped pipes", () => {
    render(<AssistantContent content={[
      "Synthetic incident report. No live operational advice.",
      "| Incident | Risk | Commercial |",
      "| :--- | ---: | :---: |",
      "| Valve \\| sensor | High | Low |",
      "",
      "Ratings are random.",
      "Incident | Risk",
      "--- | ---",
      "Pump | Medium",
      "End of report.",
    ].join("\r\n")} />);
    const tables = screen.getAllByRole("table");
    expect(tables).toHaveLength(2);
    expect(within(tables[0]).getAllByRole("columnheader")).toHaveLength(3);
    expect(within(tables[0]).getByRole("cell", { name: "Valve | sensor" })).toBeVisible();
    expect(within(tables[1]).getByRole("cell", { name: "Pump" })).toBeVisible();
    expect(screen.getByText("Ratings are random.")).toBeVisible();
    expect(screen.getByText("End of report.")).toBeVisible();
  });

  it("renders HTML and dangerous Markdown links as inert text in prose and cells", () => {
    const html = '<img src=x onerror="alert(1)">';
    const link = "[open](javascript:alert(1))";
    const { container } = render(<AssistantContent content={[
      '<script>alert(1)</script>',
      `| ${html} | Link |`,
      "| --- | --- |",
      `| <svg onload=alert(1)> | ${link} |`,
    ].join("\n")} />);
    expect(screen.getByRole("columnheader", { name: html })).toBeVisible();
    expect(screen.getByRole("cell", { name: link })).toBeVisible();
    expect(container.querySelector("script, img, svg, a, iframe")).toBeNull();
    expect(container).toHaveTextContent("<script>alert(1)</script>");
  });

  it("preserves malformed rows and does not interpret fenced examples as tables", () => {
    const { container } = render(<AssistantContent content={[
      "```markdown",
      "| Example | Risk |",
      "| --- | --- |",
      "```",
      "| Incident | Risk |",
      "| --- | --- |",
      "| Pump | High | Extra evidence |",
      "A | B",
      "invalid | separator",
    ].join("\n")} />);
    expect(screen.getAllByRole("table")).toHaveLength(1);
    expect(container.querySelector("pre")).toHaveTextContent("| Example | Risk |");
    expect(container).toHaveTextContent("| Pump | High | Extra evidence |");
    expect(container).toHaveTextContent("invalid | separator");
  });
});
