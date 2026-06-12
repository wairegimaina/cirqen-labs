#!/bin/bash
# Quick PostgreSQL Binary Test

echo "Quick PostgreSQL Binary Test"
echo "============================="
echo ""

cd "$(dirname "$0")"

# Check if we're in Cirqen directory
if [ ! -d "runtime/postgresql/bin" ]; then
    echo "❌ Error: Must run from Cirqen directory"
    echo "Usage: cd dist/Cirqen && bash test_postgres.sh"
    exit 1
fi

POSTGRES="./runtime/postgresql/bin/postgres"
DATA_DIR="$HOME/.local/share/cirqen/postgres/data"

echo "1. Checking PostgreSQL binary..."
if [ ! -f "$POSTGRES" ]; then
    echo "❌ PostgreSQL binary not found!"
    echo "Expected at: $POSTGRES"
    echo ""
    echo "Runtime directory contents:"
    ls -la runtime/
    exit 1
fi

echo "✅ Found: $POSTGRES"
echo ""

echo "2. Checking if binary is executable..."
if [ ! -x "$POSTGRES" ]; then
    echo "⚠️  Not executable, fixing..."
    chmod +x "$POSTGRES"
    chmod +x ./runtime/postgresql/bin/*
fi
echo "✅ Binary is executable"
echo ""

echo "3. Testing PostgreSQL version..."
"$POSTGRES" --version
if [ $? -eq 0 ]; then
    echo "✅ PostgreSQL binary works!"
else
    echo "❌ PostgreSQL binary failed!"
    echo ""
    echo "Checking library dependencies..."
    ldd "$POSTGRES" | grep "not found"
fi
echo ""

echo "4. Checking data directory..."
if [ -d "$DATA_DIR" ]; then
    echo "✅ Data directory exists: $DATA_DIR"

    # Check for important files
    if [ -f "$DATA_DIR/PG_VERSION" ]; then
        echo "   PostgreSQL version: $(cat $DATA_DIR/PG_VERSION)"
    fi

    # Check for lock file
    if [ -f "$DATA_DIR/postmaster.pid" ]; then
        echo "⚠️  Lock file present - removing..."
        rm "$DATA_DIR/postmaster.pid"
    fi
else
    echo "⚠️  Data directory doesn't exist: $DATA_DIR"
    echo "   Will be created on first run"
fi
echo ""

echo "5. Testing library dependencies..."
echo "Checking for missing libraries..."
MISSING=$(ldd "$POSTGRES" 2>&1 | grep "not found")
if [ -z "$MISSING" ]; then
    echo "✅ All libraries found"
else
    echo "❌ Missing libraries:"
    echo "$MISSING"
    echo ""
    echo "You may need to install:"
    echo "  sudo apt-get install libreadline8 libssl3"
fi
echo ""

echo "============================="
echo "Test complete!"
echo ""
echo "Next step: Run from Cirqen directory:"
echo "  bash diagnose_postgres.sh"
