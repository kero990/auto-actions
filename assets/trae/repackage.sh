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

echo "Copying and resizing icon..."
if [ -f "$WORK_DIR/extracted/usr/share/pixmaps/trae-cn.png" ]; then
    ICON_DEST="$WORK_DIR/deb/opt/apps/$PACKAGE_NAME/entries/icons/hicolor/256x256/apps/$PACKAGE_NAME.png"
    cp "$WORK_DIR/extracted/usr/share/pixmaps/trae-cn.png" "$ICON_DEST"
    
    # Resize icon to match directory specification (256x256)
    if command -v convert &> /dev/null; then
        convert "$ICON_DEST" \
            -resize 256x256 "$ICON_DEST"
        echo "Icon resized to 256x256"
    else
        echo "Warning: imagemagick not found, icon will keep original resolution"
    fi
fi

echo "Copying DEBIAN directory..."
cp -r "$WORK_DIR/extracted/DEBIAN" "$WORK_DIR/deb/"

echo "Updating package name in control file..."
sed -i "s/^Package: trae-cn$/Package: $PACKAGE_NAME/" "$WORK_DIR/deb/DEBIAN/control"
sed -i "s/^Provides: trae-cn$/Provides: $PACKAGE_NAME/" "$WORK_DIR/deb/DEBIAN/control"
sed -i "s/^Conflicts: trae-cn$/Conflicts: $PACKAGE_NAME/" "$WORK_DIR/deb/DEBIAN/control"
sed -i "s/^Replaces: trae-cn$/Replaces: $PACKAGE_NAME/" "$WORK_DIR/deb/DEBIAN/control"

echo "Updating hardcoded paths in desktop files..."
# 1. 重命名主 desktop 文件
if [ -f "$WORK_DIR/deb/opt/apps/$PACKAGE_NAME/entries/applications/trae-cn.desktop" ]; then
    mv "$WORK_DIR/deb/opt/apps/$PACKAGE_NAME/entries/applications/trae-cn.desktop" \
       "$WORK_DIR/deb/opt/apps/$PACKAGE_NAME/entries/applications/$PACKAGE_NAME.desktop"
fi

# 2. 重命名 url-handler desktop 文件 (Deepin 规范要求前缀一致)
if [ -f "$WORK_DIR/deb/opt/apps/$PACKAGE_NAME/entries/applications/trae-cn-url-handler.desktop" ]; then
    mv "$WORK_DIR/deb/opt/apps/$PACKAGE_NAME/entries/applications/trae-cn-url-handler.desktop" \
       "$WORK_DIR/deb/opt/apps/$PACKAGE_NAME/entries/applications/${PACKAGE_NAME}-url-handler.desktop"
fi

# 3. 批量修改所有的 desktop 文件内容 (包括主应用和 url-handler)
for desktop_file in "$WORK_DIR/deb/opt/apps/$PACKAGE_NAME/entries/applications/"*.desktop; do
    # 修改 Exec 路径（会保留原文件中的 %F 或 --open-url %U 参数）
    sed -i "s|Exec=/usr/share/trae-cn/trae-cn|Exec=/opt/apps/$PACKAGE_NAME/files/trae-cn|g" "$desktop_file"
    # 修改 Icon 字段
    sed -i "s/^Icon=trae-cn/Icon=$PACKAGE_NAME/g" "$desktop_file"
done

#在/usr加入desktop修复图标缺失
mkdir $WORK_DIR/deb/usr/share -p
cp $WORK_DIR/deb/opt/apps/$PACKAGE_NAME/entries/applications/ $WORK_DIR/deb/usr/share -rf
cp $WORK_DIR/deb/opt/apps/$PACKAGE_NAME/entries/icons/ $WORK_DIR/deb/usr/share/ -rf

echo "Updating hardcoded paths in postinst script..."
sed -i "s|ln -s /usr/share/trae-cn/bin/trae-cn|ln -s /opt/apps/$PACKAGE_NAME/files/bin/trae-cn|" "$WORK_DIR/deb/DEBIAN/postinst"
sed -i "s|APPARMOR_PROFILE_SOURCE='/usr/share/trae-cn/|APPARMOR_PROFILE_SOURCE='/opt/apps/$PACKAGE_NAME/files/|" "$WORK_DIR/deb/DEBIAN/postinst"

echo "Fixing postrm script..."
if [ -f "$WORK_DIR/deb/DEBIAN/postrm" ]; then
    sed -i 's/db_purge/. \/usr\/share\/debconf\/confmodule\n\tdb_purge/g' "$WORK_DIR/deb/DEBIAN/postrm"
fi

echo "Updating hardcoded paths in apparmor profiles..."
find "$WORK_DIR/deb/opt/apps/$PACKAGE_NAME/files" -name "apparmor-profile" -exec \
    sed -i "s|profile trae-cn /usr/share/trae-cn/trae-cn|profile trae-cn /opt/apps/$PACKAGE_NAME/files/trae-cn|" {} \;

echo "Updating hardcoded paths in bin/trae-cn script..."
sed -i "s|VSCODE_PATH=\"/usr/share/trae-cn\"|VSCODE_PATH=\"/opt/apps/$PACKAGE_NAME/files\"|" "$WORK_DIR/deb/opt/apps/$PACKAGE_NAME/files/bin/trae-cn"

if [ "${SKIP_BUILD}" = "1" ]; then
    echo "Skipping build as requested. Deb directory is at: $WORK_DIR/deb"
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
