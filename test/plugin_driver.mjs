#!/usr/bin/env bun
// Loads the real plugin module and drives one simulated tool call.
// argv: <tool> <json-args>   env: SHUNT_ROOT (optional hook root override)
import { argv } from 'process'

const pluginModule = await import(new URL('../plugin/index.ts', import.meta.url).href)
const plugin = pluginModule.default ?? pluginModule.ShuntPlugin
const hooks = await plugin({})
const before = hooks['tool.execute.before']

const tool = argv[2]
const args = JSON.parse(argv[3] ?? '{}')

try {
  await before({ tool, sessionID: 'test', callID: 'test' }, { args })
  console.log(JSON.stringify({ blocked: false }))
} catch (error) {
  console.log(JSON.stringify({ blocked: true, message: error.message }))
}
