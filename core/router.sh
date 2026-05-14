#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  FlamOS — tmux router (core/router.sh)
#
#  Evolved from tmuxSmtWdw.sh with:
#  - Aliases / shortcuts
#  - Recency scoring (most-recently-used floats to top)
#  - Levenshtein-style best-match across ALL live sessions
#  - Merges registry knowledge (sessions from services.yaml via $FLAM_SESSIONS)
#  - Falls back to creating a new window in the first session
#
#  Usage:
#    router.sh <fuzzy-name>
#    FLAM_SESSIONS="nginx:FlamNginx,maps:FlamMaps" router.sh maps
# ─────────────────────────────────────────────────────────────────────────────

TARGET_RAW="${1:-}"

if [ -z "$TARGET_RAW" ]; then
  echo "Usage: router.sh <window_or_session_name>"
  exit 1
fi

TARGET=$(echo "$TARGET_RAW" | tr '[:upper:]' '[:lower:]' | tr -d ' ')

# ── Aliases ────────────────────────────────────────────────────────────────────
declare -A ALIASES=(
  ["cf"]="cloudflare"
  ["cfd"]="cloudflare"
  ["wx"]="weather"
  ["web"]="weather"
  ["sv"]="weather"
  ["svelte"]="weather"
  ["mp"]="maps"
  ["ng"]="nginx"
  ["wd"]="watchdog"
  ["dog"]="watchdog"
  ["obs"]="observability"
  ["infra"]="nginx"
)

# Resolve alias
if [[ -n "${ALIASES[$TARGET]+x}" ]]; then
  RESOLVED="${ALIASES[$TARGET]}"
  echo "  alias: $TARGET → $RESOLVED"
  TARGET="$RESOLVED"
fi

# ── Recency file ───────────────────────────────────────────────────────────────
RECENCY_FILE="${FLAM_STATE_DIR:-$HOME/.flam}/recency.txt"
mkdir -p "$(dirname "$RECENCY_FILE")" 2>/dev/null

recency_score() {
  local name="$1"
  local score=0
  if [ -f "$RECENCY_FILE" ]; then
    local line
    line=$(grep "^${name}:" "$RECENCY_FILE" 2>/dev/null | tail -1)
    score="${line##*:}"
    score="${score:-0}"
  fi
  echo "${score:-0}"
}

record_visit() {
  local name="$1"
  local ts
  ts=$(date +%s)
  # Keep last 50 entries
  {
    grep -v "^${name}:" "$RECENCY_FILE" 2>/dev/null | tail -49
    echo "${name}:${ts}"
  } > "${RECENCY_FILE}.tmp" && mv "${RECENCY_FILE}.tmp" "$RECENCY_FILE"
}

# ── Collect all candidate session names ────────────────────────────────────────
# From live tmux + from FLAM_SESSIONS env var (injected by flam jump)
declare -A CANDIDATES  # name_lower → "session:window_index"

# From live tmux sessions
if tmux ls >/dev/null 2>&1; then
  while IFS='|' read -r SESSION IDX WNAME; do
    KEY=$(echo "$SESSION" | tr '[:upper:]' '[:lower:]' | sed 's/^flam//')
    CANDIDATES["$KEY"]="${SESSION}:${IDX}"
    # Also index window names
    WKEY=$(echo "$WNAME" | tr '[:upper:]' '[:lower:]')
    CANDIDATES["$WKEY"]="${SESSION}:${IDX}"
  done < <(tmux list-windows -a -F '#S|#I|#W' 2>/dev/null)
fi

# From registry env (format: "name:Session,name2:Session2")
if [ -n "${FLAM_SESSIONS:-}" ]; then
  IFS=',' read -ra PAIRS <<< "$FLAM_SESSIONS"
  for PAIR in "${PAIRS[@]}"; do
    NAME="${PAIR%%:*}"
    SESS="${PAIR#*:}"
    KEY=$(echo "$NAME" | tr '[:upper:]' '[:lower:]')
    CANDIDATES["$KEY"]="$SESS:"
  done
fi

# ── Fuzzy match with recency scoring ──────────────────────────────────────────
best_key=""
best_score=-1

for key in "${!CANDIDATES[@]}"; do
  score=0

  # Exact match = highest score
  if [[ "$key" == "$TARGET" ]]; then
    score=1000
  # Prefix match
  elif [[ "$key" == "${TARGET}"* ]]; then
    score=500
  # Substring: target in key
  elif [[ "$key" == *"${TARGET}"* ]]; then
    score=200
  # Substring: key in target
  elif [[ "$TARGET" == *"${key}"* ]]; then
    score=100
  else
    continue
  fi

  # Add recency bonus (0 → 50 range)
  ts=$(recency_score "$key")
  now=$(date +%s)
  if [ "$ts" -gt 0 ] 2>/dev/null; then
    age=$(( now - ts ))
    if [ "$age" -lt 3600 ]; then
      score=$(( score + 50 ))
    elif [ "$age" -lt 86400 ]; then
      score=$(( score + 20 ))
    fi
  fi

  if [ "$score" -gt "$best_score" ]; then
    best_score=$score
    best_key=$key
  fi
done

# ── Navigate or create ─────────────────────────────────────────────────────────
if [ -n "$best_key" ]; then
  TARGET_REF="${CANDIDATES[$best_key]}"
  SESSION="${TARGET_REF%%:*}"
  WIN_IDX="${TARGET_REF##*:}"

  echo "  → $best_key  (score: $best_score)"
  record_visit "$best_key"

  if [ -z "$SESSION" ] || ! tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "  session '$SESSION' not running — start it with: flam up $best_key"
    exit 2
  fi

  if [ -n "$TMUX" ]; then
    if [ -n "$WIN_IDX" ]; then
      tmux switch-client -t "${SESSION}:${WIN_IDX}"
    else
      tmux switch-client -t "$SESSION"
    fi
  else
    tmux attach-session -t "$SESSION"
  fi

  exit 0
fi

# ── No match — offer to create ─────────────────────────────────────────────────
echo "  No match for: $TARGET"
echo "  Known sessions:"
for key in "${!CANDIDATES[@]}"; do
  echo "    · $key"
done | sort

echo ""
echo "  To add '$TARGET' to the registry: edit config/services.yaml"
exit 1
