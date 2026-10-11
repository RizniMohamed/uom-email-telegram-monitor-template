#!/data/data/com.termux/files/usr/bin/sh
# Install in ~/.termux/boot/ after configuration and checkpoint setup.
export PATH="$HOME/.local/bin:$PATH"
base="${UOM_TERMUX_HOME:-$HOME/.local/share/uom-monitor}"
[ -f "$base/enabled" ] || exit 0
exec "$HOME/.local/bin/uom" start
