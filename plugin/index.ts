// Shunt plugin for OpenCode 1.x: blocks oversized full reads and bare
// read-through shell commands, delegating the decision to the same
// self-contained bash hooks the CLI wrappers use. Blocking means throwing,
// which aborts the tool call before it runs.
//
// Hook contract (exit code 2 + stderr = block, any other non-zero exit =
// fail closed so a broken install is visible instead of silently disabled):
//   check-file-size  stdin: {tool_input: {file_path, offset?, limit?}}
//   check-bash-read  stdin: {tool_input: {command: string | string[]}}
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

function hook(root, name, toolInput) {
  const script = path.join(root, 'hooks', name)
  const result = spawnSync(script, [], {
    input: JSON.stringify({ tool_input: toolInput }),
    encoding: 'utf8',
    timeout: 10_000,
  })
  if (result.error) {
    throw new Error(
      `shunt hook ${name} could not run (${result.error.code ?? result.error.message}). ` +
        'Check that shunt is installed and SHUNT_ROOT points at the shunt directory.',
    )
  }
  if (result.status !== 0) {
    const message = result.stderr.trim() || `shunt hook ${name} failed with exit code ${result.status}`
    throw new Error(message)
  }
}

export const ShuntPlugin = async () => {
  const root = process.env.SHUNT_ROOT
    ?? fileURLToPath(new URL('..', import.meta.url))
  return {
    'tool.execute.before': async (input, output) => {
      if (input.tool === 'read') {
        const toolInput = { file_path: output.args.filePath }
        if (output.args.offset !== undefined) toolInput.offset = output.args.offset
        if (output.args.limit !== undefined) toolInput.limit = output.args.limit
        hook(root, 'check-file-size', toolInput)
      }
      if (input.tool === 'bash') {
        hook(root, 'check-bash-read', { command: output.args.command })
      }
    },
  }
}

export default ShuntPlugin
