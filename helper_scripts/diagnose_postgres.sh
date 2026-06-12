#!/bin/bash
# PostgreSQL Startup Diagnostic Script

echo "=========================================="
echo "  PostgreSQL Startup Diagnostic"
echo "=========================================="

# Find Cirqen directory
CIRQEN_DIR="$PWD"
if [ ! -f "$CIRQEN_DIR/Cirqen" ]; then
    echo "❌ Not in Cirqen directory"
    echo "Please run this from dist/Cirqen/"
    exit 1
fi

echo "✅ Cirqen directory: $CIRQEN_DIR"
echo ""

# Check runtime directory
echo "1. Checking runtime directory..."
if [ ! -d "$CIRQEN_DIR/runtime" ]; then
    echo "❌ Runtime directory not found!"
    exit 1
fi
echo "✅ Runtime directory exists"
echo ""

# Check PostgreSQL binaries
echo "2. Checking PostgreSQL binaries..."
POSTGRES_BIN="$CIRQEN_DIR/runtime/postgresql/bin"

if [ ! -d "$POSTGRES_BIN" ]; then
    echo "❌ PostgreSQL bin directory not found: $POSTGRES_BIN"
    echo ""
    echo "Available in runtime:"
    ls -la "$CIRQEN_DIR/runtime/"
    exit 1
fi

echo "✅ PostgreSQL bin directory exists: $POSTGRES_BIN"
echo ""
echo "Available binaries:"
ls -lh "$POSTGRES_BIN/" | head -20
echo ""

# Check postgres executable
if [ ! -f "$POSTGRES_BIN/postgres" ]; then
    echo "❌ postgres executable not found!"
    exit 1
fi

if [ ! -x "$POSTGRES_BIN/postgres" ]; then
    echo "⚠️  postgres is not executable, fixing..."
    chmod +x "$POSTGRES_BIN/postgres"
fi

echo "✅ postgres executable found and is executable"
echo ""

# Check initdb
if [ ! -f "$POSTGRES_BIN/initdb" ]; then
    echo "❌ initdb executable not found!"
    exit 1
fi

if [ ! -x "$POSTGRES_BIN/initdb" ]; then
    echo "⚠️  initdb is not executable, fixing..."
    chmod +x "$POSTGRES_BIN/initdb"
fi

echo "✅ initdb executable found"
echo ""

# Check data directory
DATA_DIR="$HOME/.local/share/cirqen/postgres/data"
echo "3. Checking data directory..."
echo "Data directory: $DATA_DIR"

if [ ! -d "$DATA_DIR" ]; then
    echo "⚠️  Data directory doesn't exist yet"
    echo "Attempting to initialize database..."
    echo ""

    mkdir -p "$HOME/.local/share/cirqen/postgres"

    echo "Running: $POSTGRES_BIN/initdb -D $DATA_DIR"
    "$POSTGRES_BIN/initdb" -D "$DATA_DIR" -U cirqen1 -E UTF8

    if [ $? -eq 0 ]; then
        echo "✅ Database initialized successfully"
    else
        echo "❌ Database initialization failed"
        exit 1
    fi
else
    echo "✅ Data directory exists"

    # Check for lock file
    if [ -f "$DATA_DIR/postmaster.pid" ]; then
        echo "⚠️  Lock file exists: $DATA_DIR/postmaster.pid"
        echo "Content:"
        cat "$DATA_DIR/postmaster.pid"
        echo ""

        PID=$(head -1 "$DATA_DIR/postmaster.pid")
        if ps -p $PID > /dev/null 2>&1; then
            echo "⚠️  PostgreSQL is already running with PID: $PID"
        else
            echo "⚠️  Lock file is stale (process not running)"
            echo "Removing stale lock file..."
            rm "$DATA_DIR/postmaster.pid"
        fi
    fi
fi

echo ""

# Try to start PostgreSQL manually
echo "4. Testing PostgreSQL startup..."
echo "Command: $POSTGRES_BIN/postgres -D $DATA_DIR -p 2215"
echo ""
echo "Starting PostgreSQL (will run for 5 seconds)..."
echo "=========================================="

# Start PostgreSQL in background and capture output
"$POSTGRES_BIN/postgres" -D "$DATA_DIR" -p 2215 2>&1 &
PG_PID=$!

echo "PostgreSQL started with PID: $PG_PID"
sleep 5

if ps -p $PG_PID > /dev/null 2>&1; then
    echo "✅ PostgreSQL is running!"
    echo ""
    echo "Stopping test instance..."
    kill $PG_PID
    wait $PG_PID 2>/dev/null
else
    echo "❌ PostgreSQL stopped/crashed"
fi

echo ""
echo "=========================================="
echo "5. Checking PostgreSQL logs..."

if [ -d "$DATA_DIR/log" ]; then
    echo "Recent log entries:"
    echo ""
    find "$DATA_DIR/log" -type f -name "*.log" -exec tail -30 {} \; 2>/dev/null
elif [ -f "$DATA_DIR/postgresql.log" ]; then
    echo "Log file content:"
    tail -30 "$DATA_DIR/postgresql.log"
else
    echo "No PostgreSQL logs found"
fi

echo ""
echo "=========================================="
echo "6. Checking Cirqen logs..."

CIRQEN_LOGS="$HOME/.local/share/cirqen/logs"
if [ -d "$CIRQEN_LOGS" ]; then
    LATEST_LOG=$(ls -t "$CIRQEN_LOGS"/*.log 2>/dev/null | head -1)
    if [ -n "$LATEST_LOG" ]; then
        echo "Latest Cirqen log: $LATEST_LOG"
        echo "Last 50 lines:"
        echo ""
        tail -50 "$LATEST_LOG"
    else
        echo "No Cirqen logs found"
    fi
else
    echo "Cirqen logs directory not found"
fi

echo ""
echo "=========================================="
echo "Diagnostic complete!"
echo ""
echo "If PostgreSQL started successfully in the test:"
echo "  → The issue might be in how Cirqen is starting it"
echo "  → Check the Cirqen logs above for errors"
echo ""
echo "If PostgreSQL failed to start:"
echo "  → Check the error messages above"
echo "  → Check PostgreSQL logs above"
echo "=========================================="
