# shunt-opencode

Keep big files out of your OpenCode agent's context. When the agent tries to read a large file in full, a plugin hook stops it and points at a skill that hands the files to a cheaper worker model via a private, managed OpenCode server. Only the short answer comes back into the session.

Fork lineage: Spotify's [shunt](https://github.com/spotify/portal-ai-plugins/tree/main/plugins/shunt) delegates to AiKA modes on a Portal instance; [shunt-muse](https://github.com/alexanderradahl/shunt-muse) did the same with the Muse CLI; this port does it with OpenCode 1.x delegating to a headless worker. See [NOTICE](NOTICE) for what changed in each step.

## What it does

- **Plugin hooks** (`tool.execute.before` in `plugin/index.ts`) block a full `read`, or a bare `cat`/`head`/`tail`/`less`/`more` through `bash`, of any file over 350 lines. Reads with an offset or limit, piped commands and small files go through untouched.
- **`bulk-read`** sends one or more files plus a question to the worker model and prints the answer.
- **`code-write`** sends a spec plus a reference file to the worker model and writes the generated file (tests, config, stubs).
- **Skills** (`bulk-reader`, `code-writer`, `shunt-worker`) tell the agent when to call the scripts and how to operate the worker.

Every call is one shot. A follow-up question sends the files again, which costs nothing in your agent's context because they never enter it.

## How the worker call works

`scripts/lib/worker.py` manages a private OpenCode server on this machine:

1. `ensure` starts `opencode serve` on `127.0.0.1` (random port) as a background process group, or reuses the running one. Startup is **isolated**: private `XDG_CONFIG/DATA/CACHE/STATE` directories and an empty workspace, `permission: {"*": "deny"}` plus a tools-denied `shunt-worker` agent.
2. The worker inherits `OPENCODE_CONFIG_DIR`, so the company OpenCode distribution's plugins (ken) load and supply the OpenRouter provider credentials and `~…-latest` model groups. Credentials are handled by those plugins — they never appear in arguments, environment dumps, or this repo.
3. `ensure` only returns after the workspace instance answers authenticated requests (`GET /session?directory=…`), so the first delegation can't hit a half-booted server.
4. Delegations call the worker's HTTP API directly (`POST /session`, then `POST /session/{id}/message` with the instructions and corpus inlined as one text part). The corpus is line-numbered and XML-tagged into the prompt, so there is no `--file` attachment quirk, no ARG_MAX ceiling, and cited line numbers are exact.

The transport uses the server API rather than `opencode run --attach`: in 1.18.34 the CLI attach path can exit before the event stream finishes, which made success indistinguishable from a truncated failure. The API returns the finished message in one synchronous call, which is what the fail-closed response parser (`step-finish` required, non-empty text required, any error part rejected) already expects.

## Install

```sh
bash install.sh
```

This copies `hooks/`, `scripts/` and `plugin/` to `~/.local/share/shunt-opencode`, installs the three skills into `~/.config/opencode/skills/` (rewriting `${SHUNT_ROOT}` to the install path), and prints a snippet. Merge it into your `opencode.json` (global: `~/.config/opencode/opencode.json`):

```json
{ "plugin": ["/Users/you/.local/share/shunt-opencode/plugin/index.ts"] }
```

Start a new OpenCode session. The skills show up as `bulk-reader`, `code-writer` and `shunt-worker`, and oversized reads start getting blocked.

The repo also loads in-place without installing: point `"plugin"` at `/path/to/shunt-opencode/plugin/index.ts`. The plugin resolves its hooks from `SHUNT_ROOT` if set, else relative to its own location.

## Configuration

| Variable | Default | What it does |
| --- | --- | --- |
| `SHUNT_MIN_LINES` | `350` | Files longer than this get blocked |
| `SHUNT_OPENCODE_MODEL` | — | **Required.** `provider/model` delegated to, e.g. `openrouter/~google/gemini-flash-latest` |
| `SHUNT_OPENCODE_VARIANT` | unset | Model variant (provider-specific reasoning effort) |
| `SHUNT_OPENCODE_BIN` | `opencode` | Binary used to start the managed worker |
| `SHUNT_OPENCODE_SERVER` | unset | Use an already-running OpenCode server instead of the managed worker; health-checked first, its lifecycle is never touched |
| `OPENCODE_SERVER_PASSWORD` / `OPENCODE_SERVER_USERNAME` | — | Credentials for that external server |
| `SHUNT_OPENCODE_DIR` | worker workspace | Directory used as the workspace when attaching to an external server |
| `SHUNT_TIMEOUT_SECONDS` | `300` | Kill a delegation after this long |
| `SHUNT_MAX_PAYLOAD_BYTES` | `600000` | Refuse requests bigger than this; split them instead |
| `SHUNT_CATALOG_TIMEOUT_SECONDS` | `120` | How long `ensure` waits for the worker to become ready |
| `SHUNT_STATE_DIR` | `~/.local/state/shunt-opencode` | Where worker state, logs and the private workspace live |

`SHUNT_MUSE_*` variables from the previous fork are gone.

## Server lifecycle

```sh
~/.local/share/shunt-opencode/scripts/lib/worker.py ensure   # start or reuse; prints the URL
~/.local/share/shunt-opencode/scripts/lib/worker.py stop    # stop it and remove its state
```

- `ensure` is idempotent and safe to run concurrently; it prints exactly one URL.
- The worker keeps running between delegations. Run `stop` when done with heavy shunt usage or before switching `SHUNT_OPENCODE_MODEL`.
- A running-but-unhealthy worker is stopped and restarted by `ensure`.
- Corrupted or unparsable state (`worker.json`) fails closed: shunt refuses to delete it and asks you to inspect and remove `SHUNT_STATE_DIR/worker.json` explicitly. Never hand-edit state to make a command pass.
- `stop` verifies process-group ownership and waits for actual exit (SIGTERM, then SIGKILL) before removing state.
- With `SHUNT_OPENCODE_SERVER` set, shunt only attaches; it never starts, stops or restarts that server.

## Authentication and credentials

- Each managed worker generates a random password at start; every endpoint (including `/global/health`) requires HTTP basic auth, and the server binds to loopback only.
- `worker.json` (contains the password) and `worker.log` are created `0600` in `SHUNT_STATE_DIR`.
- Provider credentials come from the inherited company plugin dir and, when present, a private symlink to `~/.local/share/opencode/auth.json` inside the worker's data dir. Keys are never passed as arguments and never logged. If no credential source exists, `invoke` fails with a pointer to `opencode providers login` instead of a confusing model error.

## Data handling

- File contents are inlined into the prompt and sent through the configured provider (OpenRouter via the company setup). Check that your tier's data terms fit what you're sending.
- The worker's own logs (`SHUNT_STATE_DIR/worker.log`, `…/data/opencode/log/`) may contain prompt-derived data.
- Delegation answers are printed to stdout and, for `code-write`, written to `--target` after stripping Markdown fences.

## What it won't delegate

Same rules as upstream: debugging, editing (the agent needs exact text, so it reads the section it needs with offset/limit) and architecture calls stay with your main model. Only bulk reading and predictable boilerplate go to the worker.

## Numbers

One measured delegation on this port: a tiny corpus through `openrouter/~google/gemini-flash-latest` answered correctly with exact line citations in ~31s wall clock. Spotify reports 82–94% context savings with the original plugin on a 162K-line Java monorepo; the same mechanics apply here.

## Tests

```sh
python3 -m unittest discover -s test -p 'test_*.py'   # offline: hooks, bridge, transport, lifecycle, installer
test/hooks.sh                                         # inherited hook checks (needs jq)
SHUNT_RUN_OPENCODE_SMOKE=1 python3 -m unittest discover -s test -p 'test_worker_opencode_smoke.py'   # real local binary, no inference
```

For a provider-backed live test (real OpenRouter traffic, real cost), point `SHUNT_OPENCODE_MODEL` at a ken model group and run `scripts/bulk-read` once.

## License

Apache 2.0, same as upstream. See [NOTICE](NOTICE) for the changes from Spotify's version and from shunt-muse.