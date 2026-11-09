#!/bin/bash
# Install shunt-opencode for OpenCode 1.x.
#   - Runtime (hooks, scripts, plugin) -> ~/.local/share/shunt-opencode
#   - Skills -> ~/.config/opencode/skills/ with ${SHUNT_ROOT} rewritten
# Prints the opencode.json "plugin" snippet to add afterwards.
set -euo pipefail

SOURCE="$(cd "$(dirname "$0")" && pwd)"
SHUNT_HOME="${SHUNT_HOME:-$HOME/.local/share/shunt-opencode}"
SKILLS_DIR="${SHUNT_SKILLS_DIR:-$HOME/.config/opencode/skills}"

mkdir -p "$SHUNT_HOME"
rm -rf "$SHUNT_HOME/hooks" "$SHUNT_HOME/scripts" "$SHUNT_HOME/plugin"
cp -R "$SOURCE/hooks" "$SOURCE/scripts" "$SOURCE/plugin" "$SHUNT_HOME/"
chmod +x "$SHUNT_HOME/hooks/"* "$SHUNT_HOME/scripts/bulk-read" "$SHUNT_HOME/scripts/code-write" "$SHUNT_HOME/scripts/lib/worker.py" "$SHUNT_HOME/scripts/lib/opencode.sh"

for skill in bulk-reader code-writer shunt-worker; do
  mkdir -p "$SKILLS_DIR/$skill"
  sed "s#\${SHUNT_ROOT}#$SHUNT_HOME#g" "$SOURCE/skills/$skill/SKILL.md" > "$SKILLS_DIR/$skill/SKILL.md"
done

echo "Installed the shunt runtime to $SHUNT_HOME"
echo "Installed skills to $SKILLS_DIR (bulk-reader, code-writer, shunt-worker)"
echo
echo "Add the plugin to your opencode.json (global: ~/.config/opencode/opencode.json):"
echo
echo "  \"plugin\": [\"$SHUNT_HOME/plugin/index.ts\"]"
