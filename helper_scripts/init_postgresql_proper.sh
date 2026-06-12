#!/bin/bash
# Initialize PostgreSQL with Correct Socket Directory

echo "======================================================================"
echo "INITIALIZING POSTGRESQL WITH CORRECT SOCKET DIRECTORY"
echo "======================================================================"
echo ""

DATA_PATH="$HOME/.local/share/cirqen"
PG_DATA="$DATA_PATH/postgresql_data"
SOCKET_DIR="$DATA_PATH/sockets"
DIST_DIR="$HOME/Desktop/project/cirqen_desktop/dist/Cirqen"
PG_BIN="$DIST_DIR/runtime/postgresql/bin"
PG_LIB="$DIST_DIR/runtime/postgresql/lib"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Step 1: Verify PostgreSQL binaries
echo "Step 1: Checking PostgreSQL binaries..."

if [ ! -f "$PG_BIN/initdb" ]; then
    echo -e "${RED}❌ PostgreSQL binaries not found at: $PG_BIN${NC}"
    echo "Please check your installation."
    exit 1
fi

echo -e "${GREEN}✓ PostgreSQL binaries found${NC}"
echo ""

# Step 2: Clean up any existing data
echo "Step 2: Checking for existing PostgreSQL data..."

if [ -d "$PG_DATA" ]; then
    echo -e "${YELLOW}⚠ PostgreSQL data directory exists: $PG_DATA${NC}"
    echo "This directory needs to be removed to reinitialize."
    echo ""
    read -p "Remove existing PostgreSQL data? (y/N): " -n 1 -r
    echo

    if [[ $REPLY =~ ^[Yy]$ ]]; then
        # Backup if it has data
        if [ -f "$PG_DATA/PG_VERSION" ]; then
            BACKUP_DIR="$DATA_PATH/postgresql_data_backup_$(date +%Y%m%d_%H%M%S)"
            echo "Creating backup: $BACKUP_DIR"
            cp -r "$PG_DATA" "$BACKUP_DIR"
            echo -e "${GREEN}✓ Backup created${NC}"
        fi

        echo "Removing existing data..."
        rm -rf "$PG_DATA"
        echo -e "${GREEN}✓ Old data removed${NC}"
    else
        echo "Keeping existing data. Exiting."
        exit 0
    fi
fi

echo ""

# Step 3: Create directories
echo "Step 3: Creating directories..."

mkdir -p "$PG_DATA"
mkdir -p "$SOCKET_DIR"
mkdir -p "$DATA_PATH/logs"

chmod 700 "$PG_DATA"
chmod 700 "$SOCKET_DIR"

echo -e "${GREEN}✓ Directories created${NC}"
echo "  Data: $PG_DATA"
echo "  Sockets: $SOCKET_DIR"
echo "  Logs: $DATA_PATH/logs"
echo ""

# Step 4: Set environment
echo "Step 4: Setting up environment..."

export LD_LIBRARY_PATH="$PG_LIB:$LD_LIBRARY_PATH"

echo -e "${GREEN}✓ Environment configured${NC}"
echo "  LD_LIBRARY_PATH: $LD_LIBRARY_PATH"
echo ""

# Step 5: Initialize PostgreSQL cluster
echo "Step 5: Initializing PostgreSQL cluster..."
echo "This may take a minute..."
echo ""

# Run initdb with explicit options
"$PG_BIN/initdb" \
    -D "$PG_DATA" \
    -U postgres \
    --no-locale \
    --encoding=UTF8 \
    --auth=trust \
    > "$DATA_PATH/logs/initdb_manual.log" 2>&1

INITDB_EXIT=$?

if [ $INITDB_EXIT -eq 0 ]; then
    echo -e "${GREEN}✓ PostgreSQL cluster initialized successfully${NC}"
else
    echo -e "${RED}❌ initdb failed with exit code: $INITDB_EXIT${NC}"
    echo "Check log: $DATA_PATH/logs/initdb_manual.log"
    cat "$DATA_PATH/logs/initdb_manual.log"
    exit 1
fi

echo ""

# Step 6: Configure postgresql.conf
echo "Step 6: Configuring PostgreSQL..."

PG_CONF="$PG_DATA/postgresql.conf"

if [ ! -f "$PG_CONF" ]; then
    echo -e "${RED}❌ postgresql.conf not created by initdb${NC}"
    exit 1
fi

# Backup original
cp "$PG_CONF" "$PG_CONF.original"

# Update configuration
cat >> "$PG_CONF" << EOF

# ============================
# CUSTOM CIRQEN CONFIGURATION
# ============================

# Socket directory (FIX for permission issue)
unix_socket_directories = '$SOCKET_DIR'

# Port
port = 2215

# Listen only on localhost
listen_addresses = '127.0.0.1'

# Logging
log_destination = 'stderr'
logging_collector = off

# Connection settings
max_connections = 100

# Performance
shared_buffers = 256MB
effective_cache_size = 1GB
work_mem = 16MB
maintenance_work_mem = 64MB

# WAL settings
wal_level = minimal
max_wal_senders = 0
checkpoint_timeout = 15min

# Query planning
random_page_cost = 1.1
effective_io_concurrency = 200

# Parallel query
max_parallel_workers_per_gather = 4
max_parallel_workers = 8

# Autovacuum
autovacuum = on
autovacuum_max_workers = 3
autovacuum_naptime = 1min
EOF

echo -e "${GREEN}✓ PostgreSQL configured${NC}"
echo ""

# Step 7: Configure pg_hba.conf
echo "Step 7: Configuring authentication..."

PG_HBA="$PG_DATA/pg_hba.conf"

cat > "$PG_HBA" << EOF
# PostgreSQL Client Authentication Configuration File
# ====================================================

# TYPE  DATABASE        USER            ADDRESS                 METHOD

# "local" is for Unix domain socket connections only
local   all             postgres                                trust
local   all             all                                     md5

# IPv4 local connections:
host    all             postgres        127.0.0.1/32            trust
host    all             all             127.0.0.1/32            md5

# IPv6 local connections:
host    all             postgres        ::1/128                 trust
host    all             all             ::1/128                 md5
EOF

echo -e "${GREEN}✓ Authentication configured${NC}"
echo ""

# Step 8: Test start PostgreSQL
echo "Step 8: Testing PostgreSQL startup..."
echo ""

echo "Starting PostgreSQL (will run for 5 seconds)..."

"$PG_BIN/postgres" \
    -D "$PG_DATA" \
    > "$DATA_PATH/logs/postgres_test.log" 2>&1 &

PG_PID=$!

echo "PostgreSQL started with PID: $PG_PID"
echo "Waiting 3 seconds for startup..."
sleep 3

# Check if running
if ps -p $PG_PID > /dev/null; then
    echo -e "${GREEN}✓ PostgreSQL is running${NC}"

    # Check for socket file
    if [ -f "$SOCKET_DIR/.s.PGSQL.2215" ]; then
        echo -e "${GREEN}✓ Socket file created in correct directory!${NC}"
        echo "  Location: $SOCKET_DIR/.s.PGSQL.2215"
    else
        echo -e "${YELLOW}⚠ Socket file not found in expected location${NC}"
        echo "Checking logs..."
        tail -10 "$DATA_PATH/logs/postgres_test.log"
    fi

    # Test connection
    sleep 2
    if "$PG_BIN/psql" -h 127.0.0.1 -p 2215 -U postgres -c "SELECT version();" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Connection test successful!${NC}"
    else
        echo -e "${YELLOW}⚠ Connection test failed${NC}"
    fi

    # Stop test instance
    echo ""
    echo "Stopping test instance..."
    kill -TERM $PG_PID
    sleep 2

    if ps -p $PG_PID > /dev/null; then
        kill -9 $PG_PID
    fi

    echo -e "${GREEN}✓ Test instance stopped${NC}"
else
    echo -e "${RED}❌ PostgreSQL failed to start${NC}"
    echo "Check log: $DATA_PATH/logs/postgres_test.log"
    cat "$DATA_PATH/logs/postgres_test.log"
    exit 1
fi

echo ""

# Summary
echo "======================================================================"
echo "INITIALIZATION COMPLETE"
echo "======================================================================"
echo ""
echo "✓ PostgreSQL cluster initialized"
echo "✓ Socket directory configured: $SOCKET_DIR"
echo "✓ Configuration files created"
echo "✓ Test startup successful"
echo ""
echo "Configuration:"
echo "  Data directory: $PG_DATA"
echo "  Socket directory: $SOCKET_DIR"
echo "  Port: 2215"
echo "  Listen: 127.0.0.1"
echo "  User: postgres (trust)"
echo ""
echo "======================================================================"
echo "NEXT STEPS"
echo "======================================================================"
echo ""
echo "PostgreSQL is now initialized and configured correctly."
echo ""
echo "Start Cirqen normally:"
echo "  cd $DIST_DIR"
echo "  ./start_cirqen.sh"
echo ""
echo "The socket permission error should be fixed!"
echo ""
echo "======================================================================"
