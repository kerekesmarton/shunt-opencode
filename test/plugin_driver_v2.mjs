#!/usr/bin/env bun
// Loads the real plugin module and drives one simulated OpenCode 2.x call.
// argv: read <json-input> | hook <tool> <json-input>
// env: SHUNT_ROOT (hook root override), SHUNT_DISABLE, worker transport vars,
//      SHUNT_TEST_SESSION_CONTEXT (JSON session messages for the task brief).
import { argv, cwd, env } from 'process'

const pluginModule = await import(new URL('../plugin/index.ts', import.meta.url).href)
const plugin = pluginModule.default

// Built-in registry the transform sees; the original read returns a RAW marker.
const tools = {
  read: {
    name: 'read',
    description: 'Read a file',
    execute: async (input, context) => ({
      content: `RAW(${JSON.stringify({ input, signal: !!context?.signal })})`,
    }),
  },
}
const hooks = {}
const ctx = {
  app: { version: 'test' },
  location: { directory: cwd(), project: { directory: cwd(), id: 'test', canonical: cwd() } },
  session: {
    get: async () => ({ location: { directory: cwd() } }),
    context: async () => JSON.parse(env.SHUNT_TEST_SESSION_CONTEXT ?? '[]'),
  },
  tool: {
    hook: async (name, cb) => { hooks[name] = cb },
    transform: async (fn) => {
      fn({ get: (id) => tools[id], add: (def) => { tools[def.name] = def } })
    },
  },
}

await plugin.setup(ctx)
const wrappedRead = tools.read.execute
const scenario = argv[2]
const input = JSON.parse(argv[3] ?? '{}')
const context = {
  sessionID: env.SHUNT_TEST_SESSION_ID ?? 'ses_test',
  signal: new AbortController().signal,
}

let result
if (scenario === 'read') {
  try {
    const out = await wrappedRead(input, context)
    const content = typeof out?.content === 'string' ? out.content : JSON.stringify(out?.content ?? null)
    result = { ok: true, delegated: content.startsWith('[shunt]'), passthrough: content.startsWith('RAW('), content }
  } catch (error) {
    result = { ok: false, error: error?.message ?? String(error) }
  }
} else if (scenario === 'hook') {
  try {
    await hooks['execute.before']({ tool: argv[4], input, sessionID: context.sessionID })
    result = { blocked: false }
  } catch (error) {
    result = { blocked: true, message: error?.message ?? String(error) }
  }
} else {
  throw new Error(`unknown scenario: ${scenario}`)
}
console.log(JSON.stringify(result))