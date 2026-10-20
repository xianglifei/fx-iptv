// 把即梦生成的图标图裁切成标准 macOS 图标:
// 自动检测主体边界 -> 方形裁切 -> 圆角外透明 -> 输出 1024 master
import AppKit

let args = CommandLine.arguments
guard args.count >= 3 else {
    print("用法: make_icon <source.png> <master1024.png>")
    exit(1)
}

guard let img = NSImage(contentsOfFile: args[1]),
      let tiff = img.tiffRepresentation,
      let rep = NSBitmapImageRep(data: tiff),
      let cgSrc = rep.cgImage else {
    print("无法读取源图")
    exit(1)
}

let N = 2048
var buf = [UInt8](repeating: 0, count: N * N * 4)
let ctx = CGContext(data: &buf, width: N, height: N, bitsPerComponent: 8,
                    bytesPerRow: N * 4, space: CGColorSpaceCreateDeviceRGB(),
                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
ctx.interpolationQuality = .high
ctx.draw(cgSrc, in: CGRect(x: 0, y: 0, width: N, height: N))

// 扫描主体边界(图块是深色,背景和外围光晕是浅色)
var minX = N, maxX = 0, minY = N, maxY = 0
for y in 0..<N {
    let row = y * N * 4
    for x in 0..<N {
        let i = row + x * 4
        let alpha = Int(buf[i + 3])
        let bright = (Int(buf[i]) + Int(buf[i + 1]) + Int(buf[i + 2])) / 3
        if alpha > 230, bright < 185 {
            if x < minX { minX = x }
            if x > maxX { maxX = x }
            if y < minY { minY = y }
            if y > maxY { maxY = y }
        }
    }
}
let bw = maxX - minX + 1, bh = maxY - minY + 1
let side = max(bw, bh)
let cx = (minX + maxX) / 2, cy = (minY + maxY) / 2
print("检测到主体: \(bw)x\(bh) @ (\(minX),\(minY)), 裁切边长 \(side)")

// 裁切区域(CGI 坐标系 y 轴朝上)
let crop = CGRect(x: cx - side / 2, y: N - cy - side / 2, width: side, height: side)
guard let cropped = cgSrc.cropping(to: crop) else {
    print("裁切失败")
    exit(1)
}

// 输出 1024,圆角外透明(半径取 macOS squircle 近似 22.5%)
let OUT = 1024
let radius = CGFloat(OUT) * 0.225
let outCtx = CGContext(data: nil, width: OUT, height: OUT, bitsPerComponent: 8,
                       bytesPerRow: OUT * 4, space: CGColorSpaceCreateDeviceRGB(),
                       bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
outCtx.interpolationQuality = .high
let rect = CGRect(x: 0, y: 0, width: OUT, height: OUT)
let path = CGPath(roundedRect: rect, cornerWidth: radius, cornerHeight: radius, transform: nil)
outCtx.addPath(path)
outCtx.clip()
// 放大 4% 绘制,把图块最外圈可能残留的光晕压到可视区之外
let overscan = CGRect(x: -CGFloat(OUT) * 0.04, y: -CGFloat(OUT) * 0.04,
                      width: CGFloat(OUT) * 1.08, height: CGFloat(OUT) * 1.08)
outCtx.draw(cropped, in: overscan)
let outCG = outCtx.makeImage()!

let outRep = NSBitmapImageRep(cgImage: outCG)
guard let png = outRep.representation(using: .png, properties: [:]) else {
    print("PNG 导出失败")
    exit(1)
}
try? png.write(to: URL(fileURLWithPath: args[2]))
print("已输出 \(args[2])")
