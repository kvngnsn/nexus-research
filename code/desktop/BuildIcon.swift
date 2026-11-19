import AppKit

// Native icon packaging keeps the original logo intact and applies standard Dock framing.
func logoMark(_ source: CGImage) -> CGImage {
    let width = source.width, height = source.height
    let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                            bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
                            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
                                | CGBitmapInfo.byteOrder32Big.rawValue)!
    context.draw(source, in: CGRect(x: 0, y: 0, width: width, height: height))
    let pixels = context.data!.assumingMemoryBound(to: UInt8.self)
    var minX = width, minY = height, maxX = 0, maxY = 0
    for y in 0..<height {
        for x in 0..<width {
            let offset = (y * width + x) * 4
            if pixels[offset + 3] > 127 && max(pixels[offset], pixels[offset + 1], pixels[offset + 2]) < 128 {
                minX = min(minX, x); minY = min(minY, y)
                maxX = max(maxX, x); maxY = max(maxY, y)
            }
        }
    }
    precondition(minX < maxX && minY < maxY, "The root logo must contain a dark mark.")
    let bounds = CGRect(x: max(0, minX - 2), y: max(0, minY - 2),
                        width: min(width, maxX + 3) - max(0, minX - 2),
                        height: min(height, maxY + 3) - max(0, minY - 2))
    return context.makeImage()!.cropping(to: bounds)!
}

let arguments = CommandLine.arguments
guard arguments.count == 3, let image = NSImage(contentsOfFile: arguments[1]),
      let source = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    fatalError("Usage: BuildIcon logo.png output.iconset")
}
let mark = logoMark(source)
let logo = NSImage(cgImage: mark, size: NSSize(width: mark.width, height: mark.height))
let output = URL(fileURLWithPath: arguments[2], isDirectory: true)
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)

for base in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let size = base * scale, side = CGFloat(size)
        let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
                                      bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                      isPlanar: false, colorSpaceName: .deviceRGB,
                                      bytesPerRow: size * 4, bitsPerPixel: 32)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
        NSGraphicsContext.current!.imageInterpolation = .high
        NSColor.clear.setFill()
        NSRect(x: 0, y: 0, width: side, height: side).fill(using: .copy)
        // The white tile occupies 80% of the canvas, matching normal macOS Dock icon scale.
        let tile = NSRect(x: side * 0.1, y: side * 0.1, width: side * 0.8, height: side * 0.8)
        NSColor.white.setFill()
        NSBezierPath(roundedRect: tile, xRadius: side * 0.18, yRadius: side * 0.18).fill()
        let inner = tile.insetBy(dx: tile.width * 0.15, dy: tile.height * 0.15)
        let ratio = min(inner.width / CGFloat(mark.width), inner.height / CGFloat(mark.height))
        let width = CGFloat(mark.width) * ratio, height = CGFloat(mark.height) * ratio
        logo.draw(in: NSRect(x: (side - width) / 2, y: (side - height) / 2, width: width, height: height),
                  from: .zero, operation: .sourceOver, fraction: 1)
        NSGraphicsContext.restoreGraphicsState()
        let suffix = scale == 2 ? "@2x" : ""
        let destination = output.appendingPathComponent("icon_\(base)x\(base)\(suffix).png")
        try bitmap.representation(using: .png, properties: [:])!.write(to: destination)
    }
}
