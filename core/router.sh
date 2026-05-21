#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  FlamOS — tmux router (core/router.sh)
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

  # ── Infra ──────────────────────────────────────────────────────────────────
  ["ng"]="nginx"
  ["vt"]="vault"
  ["vault"]="vault"

  # ── Databases ──────────────────────────────────────────────────────────────
  ["dg"]="dgraph-alpha"
  ["dgraph"]="dgraph-alpha"
  ["alpha"]="dgraph-alpha"
  ["dg0"]="dgraph-zero"
  ["zero"]="dgraph-zero"

  # ── Auth stack ─────────────────────────────────────────────────────────────
  ["as"]="auth-service"
  ["auth"]="auth-service"
  ["boot"]="auth-bootstrap"
  ["bootstrap"]="auth-bootstrap"

  # ── Apps ───────────────────────────────────────────────────────────────────
  ["rw"]="root-web"
  ["root"]="root-web"
  ["web"]="root-web"
  ["chat"]="chat"
  ["maps"]="maps"
  ["games"]="games"
  ["hyp"]="hypntyz"
  ["hype"]="hypntyz"
  ["ing"]="ingestion"
  ["ingest"]="ingestion"

  # ── Gatebill / KYC engine ─────────────────────────────────────────────────
  ["gb"]="gatebill-frontend"
  ["gate"]="gatebill-frontend"
  ["kyc"]="gatebill-frontend"
  ["w1"]="gatebill-worker-1"
  ["w2"]="gatebill-worker-2"
  ["worker1"]="gatebill-worker-1"
  ["worker2"]="gatebill-worker-2"
  ["kyc1"]="gatebill-worker-1"
  ["kyc2"]="gatebill-worker-2"

  # ── CRM ────────────────────────────────────────────────────────────────────
  ["crm"]="crm-frontend"
  ["crm-api"]="crm-backend"
  ["crm-be"]="crm-backend"
  ["celery"]="crm-celery"
  ["redis"]="crm-redis"

  # ── Marketing ──────────────────────────────────────────────────────────────
  ["n8n"]="n8n"
  ["flows"]="n8n"
  ["automations"]="n8n"

  # ── Pangea gateway (entry point) ───────────────────────────────────────────
  ["pg"]="pangea-api-gateway"
  ["gw"]="pangea-api-gateway"
  ["pangea"]="pangea-api-gateway"

  # ── Pangea services ────────────────────────────────────────────────────────
  ["p-auth"]="pangea-auth"
  ["p-an"]="pangea-analytics"
  ["p-chain"]="pangea-blockchain"
  ["p-comm"]="pangea-core-comm"
  ["p-cust"]="pangea-customer"
  ["p-customs"]="pangea-customs"
  ["p-loc"]="pangea-driver-location"
  ["p-eta"]="pangea-eta"
  ["p-bus"]="pangea-event-bus"
  ["p-front"]="pangea-frontend"
  ["p-inv"]="pangea-inventory"
  ["p-map"]="pangea-map"
  ["p-moto"]="pangea-motoculture"
  ["p-notif"]="pangea-notification"
  ["p-orch"]="pangea-orch"
  ["p-order"]="pangea-order"
  ["p-pay"]="pangea-payment"
  ["p-price"]="pangea-pricing"
  ["p-prod"]="pangea-product"
  ["p-ride"]="pangea-ride-matching"
  ["p-route"]="pangea-routing"
  ["p-db"]="pangea-superbase"
  ["p-traffic"]="pangea-traffic"
  ["p-users"]="pangea-user-management"

  # ── Tunnels ────────────────────────────────────────────────────────────────
  ["cf"]="cf-root"
  ["cf-root"]="cf-root"
  ["cf-auth"]="cf-auth"
  ["cf-chat"]="cf-chat"
  ["cf-maps"]="cf-maps"
  ["cf-games"]="cf-games"
  ["cf-hyp"]="cf-hypntyz"
  ["cf-ing"]="cf-ingestion"
  ["cf-crm"]="cf-crm"

  # ── FlamOS meta ────────────────────────────────────────────────────────────
  ["wd"]="watchdog"
  ["dog"]="watchdog"
  ["panic"]="flampanic"
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
  if [ -f "$RECENCY_FILE" ]; then
    local line
    line=$(grep "^${name}:" "$RECENCY_FILE" 2>/dev/null | tail -1)
    echo "${line##*:}"
  fi
  echo "0"
}

record_visit() {
  local name="$1"
  local ts
  ts=$(date +%s)
  {
    grep -v "^${name}:" "$RECENCY_FILE" 2>/dev/null | tail -49
    echo "${name}:${ts}"
  } > "${RECENCY_FILE}.tmp" && mv "${RECENCY_FILE}.tmp" "$RECENCY_FILE"
}

# ── Collect candidates: live tmux sessions + registry env ─────────────────────
declare -A CANDIDATES  # name_lower → "session:window_index"

if tmux ls >/dev/null 2>&1; then
  while IFS='|' read -r SESSION IDX WNAME; do
    KEY=$(echo "$SESSION" | tr '[:upper:]' '[:lower:]' | sed 's/^flam//' | sed 's/^pangea/p-/')
    CANDIDATES["$KEY"]="${SESSION}:${IDX}"
    WKEY=$(echo "$WNAME" | tr '[:upper:]' '[:lower:]')
    CANDIDATES["$WKEY"]="${SESSION}:${IDX}"
  done < <(tmux list-windows -a -F '#S|#I|#W' 2>/dev/null)
fi

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

  if [[ "$key" == "$TARGET" ]]; then
    score=1000
  elif [[ "$key" == "${TARGET}"* ]]; then
    score=500
  elif [[ "$key" == *"${TARGET}"* ]]; then
    score=200
  elif [[ "$TARGET" == *"${key}"* ]]; then
    score=100
  else
    continue
  fi

  ts=$(recency_score "$key")
  now=$(date +%s)
  if [ "$ts" -gt 0 ] 2>/dev/null; then
    age=$(( now - ts ))
    if   [ "$age" -lt 3600  ]; then score=$(( score + 50 ))
    elif [ "$age" -lt 86400 ]; then score=$(( score + 20 ))
    fi
  fi

  if [ "$score" -gt "$best_score" ]; then
    best_score=$score
    best_key=$key
  fi
done

# ── Navigate ───────────────────────────────────────────────────────────────────
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
    [ -n "$WIN_IDX" ] && tmux switch-client -t "${SESSION}:${WIN_IDX}" \
                      || tmux switch-client -t "$SESSION"
  else
    tmux attach-session -t "$SESSION"
  fi

  exit 0
fi

# ── No match ───────────────────────────────────────────────────────────────────
echo "  No match for: $TARGET"
echo ""
echo "  Registered aliases:"
for key in $(echo "${!ALIASES[@]}" | tr ' ' '\n' | sort); do
  printf "    %-16s → %s\n" "$key" "${ALIASES[$key]}"
done
echo ""
echo "  To add '$TARGET': edit config/services.yaml or add an alias to core/router.sh"
exit 1
