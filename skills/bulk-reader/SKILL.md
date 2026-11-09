---
name: bulk-reader
description: "Delegate bulk file reading to a cheap worker model via headless OpenCode. Use when you need to read files >350 lines, answer questions across 3+ files, or summarize large diffs."
---

```bash
${SHUNT_ROOT}/scripts/bulk-read --question "<question>" --paths <file1> [<file2> ...]
```

Each call is independent. To ask a follow-up, ask again with the same `--paths` — the files
go to the worker model, never into your context, so re-sending them costs you nothing.

Requires `SHUNT_OPENCODE_MODEL` to be set to a configured provider/model.
See the `shunt-worker` skill for how the worker server is started and stopped.

Verify specific line numbers or exact values before using them in edits.
