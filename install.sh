#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  FlamOS install.sh
#  Run once from the FlamOS directory to wire everything up.
# ─────────────────────────────────────────────────────────────────────────────

set -e

FLAM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLAM_BIN="/usr/local/bin/flam"
ROUTER_BIN="/usr/local/bin/tm"

echo ""
echo "  ⚡ FlamOS Installer"
echo "  ─────────────────────────────────────────────"
echo "  Root: $FLAM_DIR"
echo ""

# ── Python deps ───────────────────────────────────────────────────────────────
echo "  [1/4] Installing Python dependencies..."
pip install pyyaml rich --break-system-packages -q
echo "        ✓ pyyaml, rich"

# ── Make scripts executable ───────────────────────────────────────────────────
echo "  [2/4] Setting permissions..."
chmod +x "$FLAM_DIR/flam.py"
chmod +x "$FLAM_DIR/core/router.sh"
echo "        ✓ done"

# ── Symlink flam CLI ──────────────────────────────────────────────────────────
echo "  [3/4] Linking flam → $FLAM_BIN"
ln -sf "$FLAM_DIR/flam.py" "$FLAM_BIN"
echo "        ✓ flam available globally"

# ── Symlink tm shortcut (for router directly) ─────────────────────────────────
echo "  [4/4] Linking tm → $ROUTER_BIN"
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
