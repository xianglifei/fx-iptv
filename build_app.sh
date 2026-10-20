#!/bin/zsh
# 构建「联通电视.app」独立应用包
set -e
cd "$(dirname "$0")"

APP="联通电视.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/static"

# 1. 拷贝运行资源(自包含)
cp server.py channels.m3u "$APP/Contents/Resources/"
cp static/index.html static/style.css static/app.js static/hls.min.js "$APP/Contents/Resources/static/"
[ -f "联通电视.icns" ] && cp "联通电视.icns" "$APP/Contents/Resources/"

# 2. 记录 python3 绝对路径(GUI 环境无 PATH)
PY="$(command -v python3 || echo /usr/bin/python3)"
echo "$PY" > "$APP/Contents/Resources/python_path.txt"

# 3. Info.plist
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>联通电视</string>
    <key>CFBundleDisplayName</key><string>联通电视</string>
    <key>CFBundleExecutable</key><string>联通电视</string>
    <key>CFBundleIconFile</key><string>联通电视</string>
    <key>CFBundleIdentifier</key><string>com.local.unicomtv</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSMinimumSystemVersion</key><string>12.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSAppTransportSecurity</key>
    <dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict>
</plist>
EOF

# 4. 编译 Swift 壳
swiftc -O -o "$APP/Contents/MacOS/联通电视" main.swift

# 5. 本地 ad-hoc 签名
codesign --force -s - "$APP"

echo "✓ 已生成 $APP"
