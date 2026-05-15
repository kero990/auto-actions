#!/bin/bash
set -e

# Trae CN Deepin Repackage Script
# This script repackages trae-cn.deb according to deepin package rules

ORIGINAL_DEB="$1"
if [ -z "$ORIGINAL_DEB" ]; then
    echo "Usage: $0 <original-trae-cn.deb>"
    exit 1
fi

if [ ! -f "$ORIGINAL_DEB" ]; then
    echo "Error: File $ORIGINAL_DEB not found"
    exit 1
fi

WORK_DIR="$(mktemp -d)"
PACKAGE_NAME="com.trae.app"

echo "Creating working directory: $WORK_DIR"
mkdir -p "$WORK_DIR/extracted" "$WORK_DIR/deb"

echo "Extracting original deb package..."
dpkg-deb -x "$ORIGINAL_DEB" "$WORK_DIR/extracted"
dpkg-deb -e "$ORIGINAL_DEB" "$WORK_DIR/extracted/DEBIAN"

echo "Creating deepin package directory structure..."
mkdir -p "$WORK_DIR/deb/opt/apps/$PACKAGE_NAME/entries/applications"
mkdir -p "$WORK_DIR/deb/opt/apps/$PACKAGE_NAME/entries/icons/hicolor/256x256/apps"
mkdir -p "$WORK_DIR/deb/opt/apps/$PACKAGE_NAME/files"

echo "Copying application files..."
cp -a "$WORK_DIR/extracted/usr/share/trae-cn/"* "$WORK_DIR/deb/opt/apps/$PACKAGE_NAME/files/"

echo "Copying desktop files..."
cp "$WORK_DIR/extracted/usr/share/applications/"*.desktop "$WORK_DIR/deb/opt/apps/$PACKAGE_NAME/entries/applications/"

echo "Copying icon..."
if [ -f "$WORK_DIR/extracted/usr/share/pixmaps/trae-cn.png" ]; then
    cp "$WORK_DIR/extracted/usr/share/pixmaps/trae-cn.png" "$WORK_DIR/deb/opt/apps/$PACKAGE_NAME/entries/icons/hicolor/256x256/apps/"
fi

echo "Copying DEBIAN directory..."
cp -r "$WORK_DIR/extracted/DEBIAN" "$WORK_DIR/deb/"

echo "Updating package name in control file..."
sed -i "s/^Package: trae-cn$/Package: $PACKAGE_NAME/" "$WORK_DIR/deb/DEBIAN/control"
sed -i "s/^Provides: trae-cn$/Provides: $PACKAGE_NAME/" "$WORK_DIR/deb/DEBIAN/control"
sed -i "s/^Conflicts: trae-cn$/Conflicts: $PACKAGE_NAME/" "$WORK_DIR/deb/DEBIAN/control"
sed -i "s/^Replaces: trae-cn$/Replaces: $PACKAGE_NAME/" "$WORK_DIR/deb/DEBIAN/control"

echo "Updating hardcoded paths in desktop files..."
sed -i "s|Exec=/usr/share/trae-cn/trae-cn|Exec=/opt/apps/$PACKAGE_NAME/files/trae-cn|g" "$WORK_DIR/deb/opt/apps/$PACKAGE_NAME/entries/applications/"*.desktop

echo "Updating hardcoded paths in postinst script..."
sed -i "s|ln -s /usr/share/trae-cn/bin/trae-cn|ln -s /opt/apps/$PACKAGE_NAME/files/bin/trae-cn|" "$WORK_DIR/deb/DEBIAN/postinst"
sed -i "s|APPARMOR_PROFILE_SOURCE='/usr/share/trae-cn/|APPARMOR_PROFILE_SOURCE='/opt/apps/$PACKAGE_NAME/files/|" "$WORK_DIR/deb/DEBIAN/postinst"

echo "Updating hardcoded paths in apparmor profiles..."
find "$WORK_DIR/deb/opt/apps/$PACKAGE_NAME/files" -name "apparmor-profile" -exec \
    sed -i "s|profile trae-cn /usr/share/trae-cn/trae-cn|profile trae-cn /opt/apps/$PACKAGE_NAME/files/trae-cn|" {} \;

echo "Updating hardcoded paths in bin/trae-cn script..."
sed -i "s|VSCODE_PATH=\"/usr/share/trae-cn\"|VSCODE_PATH=\"/opt/apps/$PACKAGE_NAME/files\"|" "$WORK_DIR/deb/opt/apps/$PACKAGE_NAME/files/bin/trae-cn"

if [ "${SKIP_BUILD}" = "1" ]; then
    echo "Skipping build as requested. Deb directory is at: $WORK_DIR/deb"
    # Copy deb directory to current directory for further processing
    cp -r "$WORK_DIR/deb" "./deb-repackage"
    echo "Deb directory copied to: ./deb-repackage"
else
    echo "Building new deb package..."
    OUTPUT_DEB="${PACKAGE_NAME}.deb"
    dpkg-deb -b "$WORK_DIR/deb" "$OUTPUT_DEB"
    echo "Done! Created $OUTPUT_DEB"
    echo "Package size: $(du -h "$OUTPUT_DEB" | cut -f1)"
fi

echo "Cleaning up temporary directory..."
rm -rf "$WORK_DIR"
