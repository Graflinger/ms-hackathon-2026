import type { ReactNode } from "react";

// Deliberately limited Markdown: pipe tables plus literal text/code blocks.
// All model content remains React text children, never HTML or URL attributes.
function tableCells(line: string): string[] | null {
  const cells = [""];
  let separated = false;
  const text = line.trim();
  for (let i = 0; i < text.length; i++) {
    if (
      text[i] === "\\" &&
      (text[i + 1] === "|" || text[i + 1] === "\\")
    ) {
      cells[cells.length - 1] += text[++i];
    } else if (text[i] === "|") {
      cells.push("");
      separated = true;
    } else {
      cells[cells.length - 1] += text[i];
    }
  }
  if (!separated) return null;
  if (cells[0] === "") cells.shift();
  if (cells[cells.length - 1] === "") cells.pop();
  return cells.length ? cells.map((cell) => cell.trim()) : null;
}

export function AssistantContent({ content }: { content: string }) {
  const lines = content.replace(/\r\n?/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let plain: string[] = [];
  function flush() {
    if (plain.length) {
      blocks.push(<p key={blocks.length}>{plain.join("\n")}</p>);
      plain = [];
    }
  }
  for (let i = 0; i < lines.length;) {
    const fence = lines[i].trim().match(/^(`{3,}|~{3,})/);
    if (fence) {
      flush();
      const code: string[] = [];
      i++;
      while (i < lines.length) {
        const closing = lines[i].trim();
        i++;
        if (
          closing.length >= fence[1].length &&
          [...closing].every((char) => char === fence[1][0])
        )
          break;
        code.push(lines[i - 1]);
      }
      blocks.push(
        <pre key={blocks.length}><code>{code.join("\n")}</code></pre>,
      );
      continue;
    }
    const header = tableCells(lines[i]);
    const separator = tableCells(lines[i + 1] ?? "");
    if (
      header &&
      separator &&
      header.length === separator.length &&
      separator.every((cell) => /^:?-{3,}:?$/.test(cell))
    ) {
      flush();
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length) {
        const row = tableCells(lines[i]);
        // Keep malformed rows visible as literal text rather than dropping cells.
        if (!row || row.length !== header.length) break;
        rows.push(row);
        i++;
      }
      blocks.push(
        <div
          className="table-scroll"
          key={blocks.length}
          tabIndex={0}
          role="region"
          aria-label="Agent response table"
        >
          <table>
            <thead>
              <tr>
                {header.map((cell, index) => (
                  <th scope="col" key={index}>{cell}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={index}>
                  {row.map((cell, column) => <td key={column}>{cell}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
    } else {
      plain.push(lines[i++]);
    }
  }
  flush();
  return <div className="assistant-content">{blocks}</div>;
}
