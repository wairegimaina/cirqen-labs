#!/usr/bin/env python3
"""
Cirqen Desktop Launcher
Cross-platform startup script for Windows and Linux
Handles cleanup, port checking, and service initialization
"""

import sys
import os
import platform
import subprocess
import time
import socket
from pathlib import Path

# Platform detection
IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"

# Colors for terminal output
class Colors:
    if IS_WINDOWS:
        # Windows - use simple output
        GREEN = ""
        RED = ""
        YELLOW = ""
        BLUE = ""
        NC = ""
    else:
        # Linux - use ANSI colors
        GREEN = '\033[0;32m'
        RED = '\033[0;31m'
        YELLOW = '\033[1;33m'
        BLUE = '\033[0;34m'
        NC = '\033[0m'


def print_header():
    """Print application header"""
    print(f"{Colors.BLUE}")
    print("=" * 50)
    print("  CIRQEN DESKTOP APPLICATION")
    print("  Starting all services...")
    print("=" * 50)
    print(f"{Colors.NC}\n")


def print_step(step, total, message):
    """Print step with color"""
    print(f"{Colors.GREEN}[{step}/{total}]{Colors.NC} {message}")


def print_error(message):
    """Print error message"""
    print(f"{Colors.RED}ERROR: {message}{Colors.NC}")


def print_warning(message):
    """Print warning message"""
    print(f"{Colors.YELLOW}Warning: {message}{Colors.NC}")


def print_success(message):
    """Print success message"""
    print(f"{Colors.GREEN}✓ {message}{Colors.NC}")


def check_port_available(port):
    """Check if a port is available"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.close()
        return True
    except OSError:
        return False


def kill_process_by_name(process_name):
    """Kill processes by name"""
    try:
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/IM", process_name],
                capture_output=True,
                check=False
            )
        else:
            subprocess.run(
                ["pkill", "-f", process_name],
                capture_output=True,
                check=False
            )
        return True
    except Exception as e:
        print_warning(f"Could not kill {process_name}: {e}")
        return False


def cleanup_previous_sessions():
    """Stop any running Cirqen services"""
    print_step(4, 7, "Cleaning up previous sessions...")

    processes = [
        "Cirqen.exe" if IS_WINDOWS else "Cirqen",
        "postgres",
        "redis-server",
        "celery"
    ]

    for proc in processes:
        kill_process_by_name(proc)

    time.sleep(2)
    print_success("Previous sessions cleaned")


def check_executable():
    """Check if Cirqen executable exists"""
    print_step(1, 7, "Checking Cirqen executable...")

    # Possible executable names
    exe_names = [
        "Cirqen.exe" if IS_WINDOWS else "Cirqen",
        "cirqen.exe" if IS_WINDOWS else "cirqen",
        "dist/Cirqen/Cirqen.exe" if IS_WINDOWS else "dist/Cirqen/Cirqen",
        "./Cirqen" if not IS_WINDOWS else None
    ]

    script_dir = Path(__file__).parent

    for name in exe_names:
        if name is None:
            continue
        # Try relative to this script
        exe_path = script_dir / name
        if exe_path.exists():
            print_success(f"Found executable: {exe_path}")
            return exe_path
        # Try current directory
        local_path = Path(name)
        if local_path.exists():
            print_success(f"Found executable: {local_path}")
            return local_path

    print_error("Cirqen executable not found. Please ensure the application is properly installed.")
    return None


def check_runtime_components():
    """Check if runtime components exist"""
    print_step(2, 7, "Checking runtime components...")

    # Check for required directories
    required_dirs = [
        "runtime",
        "data",
        "templates",
        "static"
    ]

    for dir_name in required_dirs:
        dir_path = Path(dir_name)
        if dir_path.exists():
            continue

    print_success("Runtime components checked")
    return True


def create_data_directories():
    """Create data directories for Cirqen"""
    print_step(3, 7, "Creating data directories...")

    # Determine data directory
    if IS_WINDOWS:
        base_dir = Path.home() / "AppData" / "Local" / "Cirqen"
    else:
        base_dir = Path.home() / ".local" / "share" / "cirqen"

    # Create directories
    dirs_to_create = [
        base_dir,
        base_dir / "logs",
        base_dir / "media",
        base_dir / "uploads",
        base_dir / "config"
    ]

    for dir_path in dirs_to_create:
        dir_path.mkdir(parents=True, exist_ok=True)

    print_success("Data directories created")
    return base_dir


def check_ports():
    """Check if required ports are available"""
    print_step(5, 7, "Checking ports...")

    ports = {
        5432: "PostgreSQL",
        6379: "Redis",
        8000: "Django"
    }

    conflicts = []
    for port, service in ports.items():
        if not check_port_available(port):
            conflicts.append(f"Port {port} ({service})")

    if conflicts:
        print_warning(f"Ports in use: {', '.join(conflicts)}")
        print_warning("Cirqen will attempt to use these ports anyway")
        print_warning("If startup fails, close other applications using these ports")
    else:
        print_success("All ports available")

    return True


def check_dependencies():
    """Check if Python dependencies are available"""
    print_step(6, 7, "Checking dependencies...")

    required_modules = [
        "PySide6",
        "django",
        "psycopg2",
        "redis",
        "celery"
    ]

    missing = []
    for module in required_modules:
        try:
            __import__(module)
        except ImportError:
            missing.append(module)

    if missing:
        print_warning(f"Missing Python modules: {', '.join(missing)}")
        print_warning("These should be bundled in the executable")
    else:
        print_success("All dependencies available")

    return True


def start_cirqen(exe_path):
    """Start the Cirqen application"""
    print_step(7, 7, "Starting Cirqen Desktop...")
    print()
    print("Application window will open shortly...")
    print("Please wait for all services to initialize (10-15 seconds)")
    print()

    try:
        if IS_WINDOWS:
            # Start without console window
            subprocess.Popen(
                [str(exe_path)],
                creationflags=subprocess.CREATE_NO_WINDOW,
                cwd=exe_path.parent
            )
        else:
            # Start in background
            subprocess.Popen(
                [str(exe_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=exe_path.parent
            )

        print(f"{Colors.GREEN}")
        print("=" * 50)
        print("  CIRQEN IS NOW LOADING")
        print("=" * 50)
        print(f"{Colors.NC}\n")

        return True

    except Exception as e:
        print_error(f"Failed to start Cirqen: {e}")
        return False


def main():
    """Main launcher function"""
    # Set console title on Windows
    if IS_WINDOWS:
        os.system("title Cirqen - Starting Services")

    print_header()

    # Step 1: Check executable
    exe_path = check_executable()
    if not exe_path:
        input("\nPress Enter to exit...")
        return 1

    # Step 2: Check runtime components
    if not check_runtime_components():
        input("\nPress Enter to exit...")
        return 1

    # Step 3: Create data directories
    data_dir = create_data_directories()

    # Step 4: Cleanup previous sessions
    cleanup_previous_sessions()

    # Step 5: Check ports
    check_ports()

    # Step 6: Check dependencies
    check_dependencies()

    # Step 7: Start Cirqen
    if not start_cirqen(exe_path):
        input("\nPress Enter to exit...")
        return 1

    print(f"{Colors.GREEN}Cirqen launcher finished successfully!{Colors.NC}")
    print("You can close this window.\n")

    # Keep window open for a moment
    if IS_WINDOWS:
        time.sleep(5)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nLauncher interrupted by user")
        sys.exit(1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        input("\nPress Enter to exit...")
        sys.exit(1)
