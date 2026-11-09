---
name: code-writer
description: "Delegate boilerplate code generation to a cheap worker model via headless OpenCode. Use for tests, config, docstrings, type stubs, or any generation where >80% is predictable from reference files."
---

```bash
# Generate and write directly to target file
${SHUNT_ROOT}/scripts/code-write --spec "<what to generate>" --reference <reference-file> --target <output-path>

# Output to stdout instead (omit --target)
${SHUNT_ROOT}/scripts/code-write --spec "<what to generate>" --reference <reference-file>
```

Each call is independent. To build on what was just generated, pass that file as the
`--reference` for the next call.

Requires `SHUNT_OPENCODE_MODEL` to be set to a configured provider/model.
See the `shunt-worker` skill for how the worker server is started and stopped.

Review the output and make surgical edits for the ~5-20% that needs your-level judgment.
