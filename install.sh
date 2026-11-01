#!/usr/bin/env bash
# Installs everything devkit-demo.py needs on Raspberry Pi OS (Bookworm or newer, with desktop).
#
# Usage:
#   ./install.sh             install packages and enable VNC
#   ./install.sh --skip-vnc  install packages only
set -euo pipefail

ENABLE_VNC=1
for arg in "$@"; do
    case "$arg" in
        --skip-vnc) ENABLE_VNC=0 ;;
        -h|--help)
            sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            exit 1
            ;;
    esac
done

if [[ $EUID -eq 0 ]]; then
    echo "Run this script as your normal user (not with sudo); it calls sudo when needed." >&2
    exit 1
fi

if ! grep -qa "Raspberry Pi" /proc/device-tree/model 2>/dev/null; then
    echo "Warning: this does not look like a Raspberry Pi. Continuing anyway."
fi

# picamera2 is tied to the system libcamera build, so every Python package
# comes from apt rather than pip to keep versions consistent.
PACKAGES=(
    python3-picamera2   # Raspberry Pi camera access
    python3-opencv      # image processing and the camera window
    python3-numpy
    python3-matplotlib  # live waveform and histogram plots
    python3-pyqt5       # Qt5Agg backend used by matplotlib
    python3-sklearn     # Gaussian mixture model fitting
    rpicam-apps         # rpicam-hello, used to check the camera
)

echo "==> Updating package lists"
sudo apt-get update

echo "==> Installing packages"
sudo apt-get install -y "${PACKAGES[@]}"

if [[ $ENABLE_VNC -eq 1 ]]; then
    if command -v raspi-config >/dev/null 2>&1; then
        echo "==> Enabling VNC server"
        sudo raspi-config nonint do_vnc 0
    else
        echo "Warning: raspi-config not found, enable VNC manually."
    fi
fi

echo "==> Checking Python modules"
/usr/bin/python3 - <<'EOF'
import importlib

modules = ["picamera2", "cv2", "numpy", "matplotlib", "sklearn", "PyQt5"]
missing = []
for name in modules:
    try:
        mod = importlib.import_module(name)
        print(f"  {name:<11} {getattr(mod, '__version__', 'ok')}")
    except Exception as exc:
        print(f"  {name:<11} FAILED ({exc})")
        missing.append(name)

if missing:
    raise SystemExit(f"Missing Python modules: {', '.join(missing)}")
EOF

echo "==> Checking camera"
if rpicam-hello --list-cameras 2>/dev/null | grep -q "^[0-9] :"; then
    rpicam-hello --list-cameras 2>/dev/null | grep "^[0-9] :"
else
    echo "Warning: no camera detected. Check the ribbon cable and reboot."
fi

echo
echo "Done. Reboot if VNC was just enabled, then run:"
echo "  python3 devkit-demo.py"
