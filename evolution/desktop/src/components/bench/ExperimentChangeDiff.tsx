import "./experiment-change-diff.css";

type DiffLine = { kind: "context" | "range" | "addition" | "deletion"; text: string };

function gitPath(path: string) {
  if (!path.startsWith('"')) return path;
  // Git prints UTF-8 filenames as octal bytes when core.quotePath is enabled.
  const escaped = { a: 7, b: 8, t: 9, n: 10, v: 11, f: 12, r: 13, '"': 34, "\\": 92 };
  return path.slice(1, -1).replace(/(?:\\[0-7]{3})+|\\./g, sequence => {
    if (sequence.length === 2) return String.fromCharCode(escaped[sequence[1] as keyof typeof escaped]);
    const bytes = sequence.split("\\").slice(1).map(octal => parseInt(octal, 8));
    return new TextDecoder().decode(Uint8Array.from(bytes));
  });
}

function parseFileDiff(file: string) {
  const [header, ...rows] = file.split("\n");
  const lines: DiffLine[] = [];
  let before = "";
  let after = "";
  let previous = "";
  let next = "";
  let change = "";
  let additions = 0;
  let deletions = 0;
  for (const row of rows) {
    if (row.startsWith("@@ ")) {
      lines.push({ kind: "range", text: row });
    } else if (lines.length) {
      const text = row.slice(1);
      if (row.startsWith("+")) {
        lines.push({ kind: "addition", text });
        additions++;
      } else if (row.startsWith("-")) {
        lines.push({ kind: "deletion", text });
        deletions++;
      } else if (row.startsWith(" ")) {
        lines.push({ kind: "context", text });
      } else if (row === "\\ No newline at end of file") {
        lines.push({ kind: "range", text: "上一行在文件末尾没有换行" });
      }
    } else if (row.startsWith("--- ")) {
      before = gitPath(row.slice(4).split("\t", 1)[0]);
    } else if (row.startsWith("+++ ")) {
      after = gitPath(row.slice(4).split("\t", 1)[0]);
    } else if (row.startsWith("rename from ")) {
      previous = gitPath(row.slice(12));
    } else if (row.startsWith("rename to ")) {
      next = gitPath(row.slice(10));
    } else if (row.startsWith("new file mode ")) {
      change = "新增文件";
    } else if (row.startsWith("deleted file mode ")) {
      change = "删除文件";
    } else if (row.startsWith("new mode ")) {
      change = "文件权限改变";
    } else if (!change && (row.startsWith("Binary files ") || row === "GIT binary patch")) {
      change = "二进制内容改变";
    }
  }
  const path = next || (after === "/dev/null" ? before.slice(2) : after.slice(2))
    || gitPath(header.match(/ ("b\/(?:[^"\\]|\\.)*"|b\/.*)$/)?.[1] ?? "").slice(2);
  return { path, previous, change, additions, deletions, lines };
}

export function ExperimentChangeDiff({ diff }: { diff: string }) {
  const files = diff.replace(/\r\n/g, "\n").split(/(?=^diff --git )/m).filter(file => file.trim()).map(parseFileDiff);
  return <div className="experiment-change-diff" role="region" aria-label="文件差异" tabIndex={0}>
    {files.map(({ path, previous, change, additions, deletions, lines }, index) => <div key={index} className="experiment-change-file">
      <div className="experiment-change-file-heading">
        <h3><code>{path || "文件改动"}</code></h3>
        {!!lines.length && <span className="experiment-change-counts">
          <span className="addition" aria-label={`新增 ${additions} 行`}>+{additions}</span>
          <span className="deletion" aria-label={`删除 ${deletions} 行`}>−{deletions}</span>
        </span>}
      </div>
      {previous && <p className="experiment-change-message">原路径 <code>{previous}</code></p>}
      {lines.length ? <div className="experiment-change-lines">
        {lines.map(({ kind, text }, line) => {
          const Text = kind === "addition" ? "ins" : kind === "deletion" ? "del" : "span";
          const mark = kind === "addition" ? "+" : kind === "deletion" ? "−" : "";
          return <div key={line} className={kind}>
            {kind !== "range" && <span aria-hidden="true">{mark}</span>}
            <Text>{text || "\u00a0"}</Text>
          </div>;
        })}
      </div> : !previous && <p className="experiment-change-message">{change || "没有可显示的文本行差异。"}</p>}
    </div>)}
  </div>;
}
