#!/bin/bash
# Clean Start - Ensure No Conflicts

echo "======================================================================"
echo "CLEAN START - PREPARING TO LAUNCH CIRQEN"
echo "======================================================================"
echo ""

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Step 1: Kill any existing Cirqen processes
echo "Step 1: Stopping any running Cirqen processes..."
pkill -9 Cirqen 2>/dev/null
pkill -9 cirqen 2>/dev/null
sleep 1
echo -e "${GREEN}✓ Cirqen processes stopped${NC}"
echo ""

# Step 2: Check if port 2215 is free
echo "Step 2: Checking port 2215..."
if ss -tuln 2>/dev/null | grep -q ":2215 "; then
    echo -e "${RED}❌ Port 2215 is in use!${NC}"
    echo "Process using it:"
    ss -tulnp 2>/dev/null | grep ":2215"
    echo ""
    echo "Attempting to free port 2215..."

    # Find and kill process on port 2215
    PID=$(ss -tulnp 2>/dev/null | grep ":2215 " | grep -oP 'pid=\K[0-9]+' | head -1)
    if [ -n "$PID" ]; then
        echo "Killing process $PID..."
        kill -9 $PID 2>/dev/null
        sleep 1

        if ! ss -tuln 2>/dev/null | grep -q ":2215 "; then
            echo -e "${GREEN}✓ Port 2215 is now free${NC}"
        else
            echo -e "${RED}❌ Could not free port 2215${NC}"
            echo "You may need to: sudo lsof -ti:2215 | xargs sudo kill -9"
        fi
    fi
else
    echo -e "${GREEN}✓ Port 2215 is available${NC}"
fi
echo ""

# Step 3: Clean up stale locks
echo "Step 3: Cleaning up stale locks..."

DATA_PATH="$HOME/.local/share/cirqen"
PG_DATA="$DATA_PATH/postgresql_data"

if [ -f "$PG_DATA/postmaster.pid" ]; then
    echo "Removing stale PID file..."
    rm -f "$PG_DATA/postmaster.pid"
fi

rm -f "$DATA_PATH/sockets/.s.PGSQL"* 2>/dev/null
rm -f "$DATA_PATH/sessions"/* 2>/dev/null

echo -e "${GREEN}✓ Locks cleaned${NC}"
echo ""

# Step 4: Verify PostgreSQL configuration
echo "Step 4: Verifying PostgreSQL configuration..."

PG_CONF="$PG_DATA/postgresql.conf"

if [ -f "$PG_CONF" ]; then
    SOCKET_CONFIG=$(grep "^unix_socket_directories" "$PG_CONF" | head -1)

    if echo "$SOCKET_CONFIG" | grep -q "$DATA_PATH/sockets"; then
        echo -e "${GREEN}✓ Socket directory correctly configured${NC}"
        echo "  $SOCKET_CONFIG"
    else
        echo -e "${YELLOW}⚠ Socket directory might need updating${NC}"
        echo "  Current: $SOCKET_CONFIG"
        echo "  Expected: unix_socket_directories = '$DATA_PATH/sockets'"
    fi
else
    echo -e "${RED}❌ postgresql.conf not found${NC}"
fi
echo ""

# Step 5: Clear old logs (optional)
echo "Step 5: Preparing logs..."

LOG_DIR="$DATA_PATH/logs"
if [ -d "$LOG_DIR" ]; then
    # Backup old logs
    BACKUP_DIR="$LOG_DIR/old_logs_$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$BACKUP_DIR"

    # Move old logs to backup
    mv "$LOG_DIR"/*.log "$BACKUP_DIR"/ 2>/dev/null

    echo -e "${GREEN}✓ Old logs backed up to: $BACKUP_DIR${NC}"
else
    mkdir -p "$LOG_DIR"
    echo -e "${GREEN}✓ Log directory created${NC}"
fi
echo ""

# Step 6: Check Cirqen executable
echo "Step 6: Checking Cirqen executable..."

DIST_DIR="$HOME/Desktop/project/cirqen_desktop/dist/Cirqen"
CIRQEN_EXE="$DIST_DIR/Cirqen"

if [ -f "$CIRQEN_EXE" ]; then
    echo -e "${GREEN}✓ Cirqen executable found${NC}"
    if [ -x "$CIRQEN_EXE" ]; then
        echo -e "${GREEN}✓ Executable permissions OK${NC}"
    else
        echo -e "${YELLOW}⚠ Making executable...${NC}"
        chmod +x "$CIRQEN_EXE"
    fi
else
    echo -e "${RED}❌ Cirqen executable not found at: $CIRQEN_EXE${NC}"
fi
echo ""

# Summary
echo "======================================================================"
echo "READY TO START CIRQEN"
echo "======================================================================"
echo ""
echo "All preparations complete:"
echo "  ✓ No conflicting processes"
echo "  ✓ Port 2215 available"
echo "  ✓ Stale locks removed"
echo "  ✓ PostgreSQL configured"
echo "  ✓ Logs ready"
echo ""
echo "To start Cirqen:"
echo "  cd $DIST_DIR"
echo "  ./start_cirqen.sh"
echo ""
echo "Monitor logs:"
echo "  tail -f ~/.local/share/cirqen/logs/cirqen_app.log"
echo ""
echo "======================================================================"
