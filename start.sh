#!/bin/bash
set -e
cd "$(dirname "$0")"

GH_PORT=8991
DJANGO_PORT=8000
LOG_DIR="/tmp/bikeostrava"

mkdir -p "$LOG_DIR"

# ── Colours ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC}  $*"; }
info() { echo -e "${CYAN}→${NC}  $*"; }
warn() { echo -e "${YELLOW}!${NC}  $*"; }
fail() { echo -e "${RED}✗${NC}  $*"; exit 1; }

# ── Activate Python environment ──────────────────────────────────────────────
# Supports: venv, conda, or system Python
if [ -d ".venv" ]; then
    source .venv/bin/activate 2>/dev/null && ok "Activated .venv"
elif [ -n "$CONDA_DEFAULT_ENV" ]; then
    ok "Using conda env '$CONDA_DEFAULT_ENV'"
elif [ -n "$VIRTUAL_ENV" ]; then
    ok "Using virtualenv '$VIRTUAL_ENV'"
else
    warn "No virtual environment detected - using system Python"
fi

PYTHON="python"
$PYTHON -c "import django" 2>/dev/null || fail "Django not found. Install dependencies: pip install -r requirements.txt"

echo ""
echo -e "${CYAN}🚲  BikeOstrava - starting up${NC}"
echo "────────────────────────────────────"

# ── Cleanup on Ctrl+C ────────────────────────────────────────────────────────
cleanup() {
    echo ""
    info "Shutting down…"
    [ -n "$GH_PID" ]     && kill "$GH_PID"     2>/dev/null && ok "GraphHopper stopped"
    [ -n "$DJANGO_PID" ] && kill "$DJANGO_PID" 2>/dev/null && ok "Django stopped"
    exit 0
}
trap cleanup INT TERM

# ── 1. GraphHopper (optional) ────────────────────────────────────────────────
if curl -sf "http://localhost:$GH_PORT/health" > /dev/null 2>&1; then
    ok "GraphHopper already running on :$GH_PORT"
elif [ -f "graphhopper/graphhopper-web.jar" ] && ls graphhopper/*.osm.pbf >/dev/null 2>&1; then
    info "Starting GraphHopper…"
    cd graphhopper
    java -Xmx512m -Xms256m \
        -jar graphhopper-web.jar \
        server config.yml \
        > "$LOG_DIR/graphhopper.log" 2>&1 &
    GH_PID=$!
    cd ..

    # Wait up to 20 s for GH to be ready
    for i in $(seq 1 20); do
        sleep 1
        if curl -sf "http://localhost:$GH_PORT/health" > /dev/null 2>&1; then
            ok "GraphHopper ready on http://localhost:$GH_PORT  (pid $GH_PID)"
            break
        fi
        if ! kill -0 "$GH_PID" 2>/dev/null; then
            fail "GraphHopper crashed - check $LOG_DIR/graphhopper.log"
        fi
        [ "$i" -eq 20 ] && fail "GraphHopper did not start in time - check $LOG_DIR/graphhopper.log"
    done
else
    warn "GraphHopper not set up - using OSRM fallback (see README for setup)"
fi

# ── 2. Django migrations (fast no-op if already applied) ────────────────────
info "Checking migrations…"
$PYTHON manage.py migrate --noinput -v 0 > /dev/null 2>&1 && ok "Migrations up to date"

# ── 3. Accident data - auto-refresh every 35 days ────────────────────────────
ACCIDENT_STAMP="$LOG_DIR/accidents_loaded"
COUNT=$($PYTHON -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE','bikeostrava.settings')
django.setup()
from routing.models import AccidentPoint
print(AccidentPoint.objects.count())
" 2>/dev/null || echo 0)

NEEDS_LOAD=false
if [ "$COUNT" -eq 0 ]; then
    NEEDS_LOAD=true
    info "First load of accident data from policie.gov.cz…"
elif [ ! -f "$ACCIDENT_STAMP" ]; then
    NEEDS_LOAD=true
    info "Load stamp not found - refreshing accident data…"
else
    DAYS_OLD=$(( ( $(date +%s) - $(date -r "$ACCIDENT_STAMP" +%s) ) / 86400 ))
    if [ "$DAYS_OLD" -ge 35 ]; then
        NEEDS_LOAD=true
        info "Accident data loaded $DAYS_OLD days ago - refreshing…"
    else
        ok "Accident data up to date ($COUNT points, loaded $DAYS_OLD days ago)"
    fi
fi

if [ "$NEEDS_LOAD" = true ]; then
    $PYTHON manage.py load_accidents --clear --months 24 > "$LOG_DIR/accidents.log" 2>&1 \
        && {
            touch "$ACCIDENT_STAMP"
            ok "Accident data loaded"
            $PYTHON -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE','bikeostrava.settings')
django.setup()
from routing.models import RouteCache
RouteCache.objects.all().delete()
" 2>/dev/null && ok "Route cache cleared"
        } \
        || warn "Failed to load accident data - see $LOG_DIR/accidents.log"
fi

# ── 4. Django dev server ──────────────────────────────────────────────────────
LAN_IP=$(python -c "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('8.8.8.8',80)); print(s.getsockname()[0]); s.close()" 2>/dev/null || echo "localhost")
info "Starting Django on http://$LAN_IP:$DJANGO_PORT …"
$PYTHON manage.py runserver "0.0.0.0:$DJANGO_PORT" > "$LOG_DIR/django.log" 2>&1 &
DJANGO_PID=$!

sleep 2
if ! kill -0 "$DJANGO_PID" 2>/dev/null; then
    fail "Django failed to start - check $LOG_DIR/django.log"
fi

echo "────────────────────────────────────"
ok "All systems up!"
echo ""
echo -e "  ${GREEN}App:${NC}          http://localhost:$DJANGO_PORT  /  http://$LAN_IP:$DJANGO_PORT"
if [ -n "$GH_PID" ] || curl -sf "http://localhost:$GH_PORT/health" > /dev/null 2>&1; then
    echo -e "  ${GREEN}GraphHopper:${NC}  http://localhost:$GH_PORT"
fi
echo -e "  ${GREEN}Logs:${NC}         $LOG_DIR/"
echo ""
echo -e "  Press ${YELLOW}Ctrl+C${NC} to stop everything"
echo ""

# ── Tail Django output to terminal ───────────────────────────────────────────
tail -f "$LOG_DIR/django.log" &
TAIL_PID=$!

wait "$DJANGO_PID"
kill "$TAIL_PID" 2>/dev/null
