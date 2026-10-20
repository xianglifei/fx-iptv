#!/bin/zsh
# 从 icon-master.png 生成全部尺寸并编译 联通电视.icns
# 注意:不用循环变量拼接,避免 zsh 不分词导致的静默失败
set -e
cd "$(dirname "$0")"

ICONSET=/tmp/fx-iptv.iconset
rm -rf "$ICONSET"
mkdir -p "$ICONSET"

sips -z 16 16    icon-master.png --out "$ICONSET/icon_16x16.png"      > /dev/null
sips -z 32 32    icon-master.png --out "$ICONSET/icon_16x16@2x.png"   > /dev/null
sips -z 32 32    icon-master.png --out "$ICONSET/icon_32x32.png"      > /dev/null
sips -z 64 64    icon-master.png --out "$ICONSET/icon_32x32@2x.png"   > /dev/null
sips -z 128 128  icon-master.png --out "$ICONSET/icon_128x128.png"    > /dev/null
sips -z 256 256  icon-master.png --out "$ICONSET/icon_128x128@2x.png" > /dev/null
sips -z 256 256  icon-master.png --out "$ICONSET/icon_256x256.png"    > /dev/null
sips -z 512 512  icon-master.png --out "$ICONSET/icon_256x256@2x.png" > /dev/null
sips -z 512 512  icon-master.png --out "$ICONSET/icon_512x512.png"    > /dev/null
cp icon-master.png "$ICONSET/icon_512x512@2x.png"

iconutil -c icns "$ICONSET" -o "联通电视.icns"
echo "✓ 联通电视.icns 已从 icon-master.png 重新生成"
