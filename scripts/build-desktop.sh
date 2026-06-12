#!/bin/bash
set -e

echo "=== NoSlip Desktop App Build Script ==="

# 1. Resolve workspace root
WORKSPACE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$WORKSPACE_DIR"

echo "Workspace directory: $WORKSPACE_DIR"

# 2. Install dependencies (electron, electron-builder)
echo "Installing Electron packages..."
npm install

# 3. Compile Next.js production bundle
echo "Building Next.js application..."
npm run build

# 4. Compile Desktop Packages (macOS DMG & Windows EXE)
echo "Compiling DMG and EXE desktop packages..."
npx electron-builder --mac --win

# 5. Distribute artifacts to user's Downloads directory
DOWNLOADS_TARGET="$HOME/Downloads/noslip-desktop"
echo "Creating download directory: $DOWNLOADS_TARGET"
mkdir -p "$DOWNLOADS_TARGET"

echo "Copying installers to Downloads directory..."
cp dist-desktop/*.dmg "$DOWNLOADS_TARGET/" 2>/dev/null || true
cp dist-desktop/*.exe "$DOWNLOADS_TARGET/" 2>/dev/null || true

echo "=== Build Completed successfully! ==="
echo "Installers are now ready at: $DOWNLOADS_TARGET"
ls -la "$DOWNLOADS_TARGET"
