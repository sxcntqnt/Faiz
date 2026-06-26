#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  FlamOS install.sh
#  Run once from the FlamOS directory to wire everything up.
# ─────────────────────────────────────────────────────────────────────────────

set -e

FLAM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLAM_BIN="/usr/local/bin/flam"
ROUTER_BIN="/usr/local/bin/tm"

source "$FLAM_DIR/internals/flamos_deps.sh"
 

echo ""
echo "  ⚡ FlamOS Installer"
echo "  ─────────────────────────────────────────────"
echo "  Root: $FLAM_DIR"
echo ""
 

echo "  [1/5] Installing system dependencies..."
install_flamos_dependencies
echo "        ✓ dependencies installed"

# ── Python deps ───────────────────────────────────────────────────────────────
echo "  [2/5] Installing Python dependencies..."
pip install pyyaml rich --break-system-packages -q
echo "        ✓ pyyaml, rich"

# ── Make scripts executable ───────────────────────────────────────────────────
echo "  [3/5] Setting permissions..."
chmod +x "$FLAM_DIR/flam.py"
chmod +x "$FLAM_DIR/core/router.sh"
echo "        ✓ done"

# ── Symlink flam CLI ──────────────────────────────────────────────────────────
echo "  [4/5] Linking flam → $FLAM_BIN"
ln -sf "$FLAM_DIR/flam.py" "$FLAM_BIN"
echo "        ✓ flam available globally"

# ── Symlink tm shortcut (for router directly) ─────────────────────────────────
echo "  [5/5] Linking tm → $ROUTER_BIN"
cat > "$ROUTER_BIN" << EOF
#!/usr/bin/env bash
# tm — shortcut for: flam jump <name>
exec flam jump "\$@"
EOF
chmod +x "$ROUTER_BIN"
echo "        ✓ tm available globally"

# ── State dir ─────────────────────────────────────────────────────────────────
mkdir -p "$FLAM_DIR/state" "$FLAM_DIR/logs"
export FLAM_STATE_DIR="$HOME/.flam"
mkdir -p "$FLAM_STATE_DIR"

# ── Shell env hint ────────────────────────────────────────────────────────────
echo ""
echo "  ─────────────────────────────────────────────"
echo "  ✓ FlamOS installed."
echo ""
echo "  Add to your ~/.bashrc or ~/.zshrc:"
echo ""
echo "    export FLAM_ROOT=\"$FLAM_DIR\""
echo "    export FLAM_STATE_DIR=\"\$HOME/.flam\""
echo ""
echo "  Quick start:"
echo "    flam list               # show all services"
echo "    flam up                 # start everything"
echo "    flam dash               # live dashboard"
echo "    flam jump maps          # navigate to maps session"
echo "    tm cf                   # shorthand → cloudflare session"
echo "    flam watch              # start watchdog daemon"
echo "    flam save               # snapshot workspace"
echo "    flam restore            # resurrect after reboot"
echo "    flam panic              # open all critical log panes"
echo "    flam doctor             # health check report"
echo ""
