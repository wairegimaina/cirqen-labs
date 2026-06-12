#!/bin/bash
# PostgreSQL User Setup Script for Cirqen
# Creates the required postgres and cirqen1 users

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}======================================================================${NC}"
echo -e "${YELLOW}  CIRQEN POSTGRESQL USER SETUP${NC}"
echo -e "${YELLOW}======================================================================${NC}"
echo

# Correct paths
CIRQEN_DIR="/home/tails/Desktop/cirqen_desktop/dist/Cirqen"
PG_BIN="${CIRQEN_DIR}/runtime/postgresql/bin"
DATA_DIR="${HOME}/.local/share/cirqen/postgres"
SOCKET_DIR="${HOME}/.local/share/cirqen/sockets"
PORT=2215

# Set library path
export LD_LIBRARY_PATH="${CIRQEN_DIR}/runtime/postgresql/lib:${LD_LIBRARY_PATH}"

echo "Configuration:"
echo "  PostgreSQL Bin: ${PG_BIN}"
echo "  Data Directory: ${DATA_DIR}"
echo "  Port: ${PORT}"
echo

# Step 1: Configure pg_hba.conf for trust authentication (temporary)
echo -e "${YELLOW}[1/5] Configuring trust authentication (temporary)...${NC}"
cat > "${DATA_DIR}/pg_hba.conf" << 'EOF'
# TYPE  DATABASE        USER            ADDRESS                 METHOD
# Temporary trust for setup
local   all             all                                     trust
host    all             all             127.0.0.1/32            trust
host    all             all             ::1/128                 trust
EOF
echo -e "${GREEN}✓ pg_hba.conf configured for trust auth${NC}"
echo

# Step 2: Check if PostgreSQL is running
echo -e "${YELLOW}[2/5] Checking PostgreSQL status...${NC}"
if "${PG_BIN}/pg_isready" -h 127.0.0.1 -p ${PORT} > /dev/null 2>&1; then
    echo -e "${GREEN}✓ PostgreSQL is running${NC}"
    NEED_START=false
else
    echo -e "${YELLOW}⚠ PostgreSQL is not running, starting it...${NC}"
    NEED_START=true

    # Start PostgreSQL
    "${PG_BIN}/pg_ctl" \
        -D "${DATA_DIR}" \
        -l "${HOME}/.local/share/cirqen/logs/postgres.log" \
        -o "-p ${PORT}" \
        start

    # Wait for it to start
    echo "Waiting for PostgreSQL to start..."
    sleep 5

    # Verify it started
    if "${PG_BIN}/pg_isready" -h 127.0.0.1 -p ${PORT} > /dev/null 2>&1; then
        echo -e "${GREEN}✓ PostgreSQL started successfully${NC}"
    else
        echo -e "${RED}✗ PostgreSQL failed to start${NC}"
        echo "Check logs: tail ~/.local/share/cirqen/logs/postgres.log"
        exit 1
    fi
fi
echo

# Step 3: Get the current system user to use for initial connection
CURRENT_USER=$(whoami)
echo -e "${YELLOW}[3/5] Creating PostgreSQL users...${NC}"
echo "Connecting as system user: ${CURRENT_USER}"

# Create users using the system user for initial connection
"${PG_BIN}/psql" \
    -h 127.0.0.1 \
    -p ${PORT} \
    -U ${CURRENT_USER} \
    -d postgres << 'EOSQL'

-- Check if postgres user exists, create if not
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgres') THEN
        CREATE ROLE postgres WITH LOGIN PASSWORD 'postgres' SUPERUSER CREATEDB CREATEROLE;
        RAISE NOTICE 'Created postgres user';
    ELSE
        RAISE NOTICE 'postgres user already exists';
    END IF;
END
$$;

-- Check if cirqen1 user exists, create if not
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cirqen1') THEN
        CREATE ROLE cirqen1 WITH LOGIN PASSWORD 'Btwelvetech@2024' SUPERUSER CREATEDB CREATEROLE;
        RAISE NOTICE 'Created cirqen1 user';
    ELSE
        RAISE NOTICE 'cirqen1 user already exists';
        -- Update password just in case
        ALTER ROLE cirqen1 WITH PASSWORD 'Btwelvetech@2024';
        RAISE NOTICE 'Updated cirqen1 password';
    END IF;
END
$$;

-- Check if cirqen1 database exists, create if not
SELECT 'Checking cirqen1 database...' AS status;
CREATE DATABASE cirqen1 OWNER cirqen1;

-- List all users
SELECT 'Users created:' AS status;
\du

EOSQL

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Users created successfully${NC}"
else
    echo -e "${RED}✗ Failed to create users${NC}"
    exit 1
fi
echo

# Step 4: Restore pg_hba.conf to use passwords
echo -e "${YELLOW}[4/5] Configuring password authentication...${NC}"
cat > "${DATA_DIR}/pg_hba.conf" << 'EOF'
# TYPE  DATABASE        USER            ADDRESS                 METHOD

# System superuser and cirqen1 (trust for local development)
local   all             postgres                                trust
local   all             cirqen1                                 trust
host    all             postgres        127.0.0.1/32            trust
host    all             cirqen1         127.0.0.1/32            trust

# Other users require password
local   all             all                                     md5
host    all             all             127.0.0.1/32            md5
host    all             all             ::1/128                 md5
EOF
echo -e "${GREEN}✓ pg_hba.conf configured for password auth${NC}"
echo

# Step 5: Reload PostgreSQL configuration
echo -e "${YELLOW}[5/5] Reloading PostgreSQL configuration...${NC}"
"${PG_BIN}/psql" \
    -h 127.0.0.1 \
    -p ${PORT} \
    -U ${CURRENT_USER} \
    -d postgres \
    -c "SELECT pg_reload_conf();" > /dev/null 2>&1

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Configuration reloaded${NC}"
else
    echo -e "${YELLOW}⚠ Could not reload config (will apply on restart)${NC}"
fi
echo

# Final verification
echo -e "${YELLOW}Verifying setup...${NC}"
"${PG_BIN}/psql" -h 127.0.0.1 -p ${PORT} -U cirqen1 -d cirqen1 -c "SELECT 'Connection successful!' AS status;" > /dev/null 2>&1

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Can connect as cirqen1${NC}"
else
    echo -e "${YELLOW}⚠ Could not verify cirqen1 connection${NC}"
fi

echo
echo -e "${GREEN}======================================================================${NC}"
echo -e "${GREEN}  SETUP COMPLETE!${NC}"
echo -e "${GREEN}======================================================================${NC}"
echo
echo "PostgreSQL Users Created:"
echo "  • postgres (password: postgres)"
echo "  • cirqen1  (password: Btwelvetech@2024)"
echo
echo "Database Created:"
echo "  • cirqen1 (owner: cirqen1)"
echo
echo "Connection Details:"
echo "  Host:     127.0.0.1"
echo "  Port:     ${PORT}"
echo "  Database: cirqen1"
echo "  User:     cirqen1"
echo "  Password: Btwelvetech@2024"
echo
echo -e "${GREEN}You can now start Cirqen!${NC}"
echo "  cd ~/Desktop/cirqen_desktop/dist/Cirqen"
echo "  ./start_cirqen.sh"
echo

# If we started PostgreSQL, ask if user wants to keep it running
if [ "${NEED_START}" = "true" ]; then
    echo -e "${YELLOW}PostgreSQL is currently running.${NC}"
    echo "Keep it running for Cirqen? (y/n)"
    read -r response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        echo "Stopping PostgreSQL..."
        "${PG_BIN}/pg_ctl" -D "${DATA_DIR}" stop
        echo -e "${GREEN}✓ PostgreSQL stopped${NC}"
        echo "Start it again with: bash start_postgres_manual.sh"
    fi
fi

echo
