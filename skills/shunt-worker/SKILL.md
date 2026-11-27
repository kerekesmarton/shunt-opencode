---
name: shunt-worker
description: "Operate the shunt worker server for OpenCode: start (ensure), stop, inspect state, and recover from failures. Use when a shunt script fails to start the worker, when cleaning up, or before/after long offline periods."
---

The shunt runtime talks to a private, loopback-only OpenCode server it manages itself.
All lifecycle operations go through one helper — never start, kill, or configure the
worker by hand.

```bash
# Start the managed worker (or reuse the running one) and print its URL
${SHUNT_ROOT}/scripts/lib/worker.py ensure

# Stop the managed worker and remove its state
${SHUNT_ROOT}/scripts/lib/worker.py stop
```

Rules:

- `ensure` is idempotent and safe to run concurrently; it prints exactly one URL.
- The worker runs in an isolated workspace with all tools denied and no global
  plugins or skills; provider credentials are reused through a private symlink,
  never copied or logged.
- State lives in `~/.local/state/shunt-opencode/worker.json`. If a command reports
  `Managed worker state is invalid`, inspect that file, remove it explicitly, and
  run `ensure` again — never hand-edit state to make a command pass.
- To point shunt at an already-running OpenCode server instead, set
  `SHUNT_OPENCODE_SERVER` (plus `OPENCODE_SERVER_PASSWORD` when it authenticates);
  shunt will health-check it and never manage its lifecycle.
- The worker keeps running between calls. Run `stop` when done with heavy shunt
  usage (for example before shutting the machine down) or when switching
  `SHUNT_OPENCODE_MODEL`.
