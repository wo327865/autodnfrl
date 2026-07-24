import CoreGraphics
import Foundation

struct MovementDirection: Equatable {
    let horizontal: CGKeyCode?
    let vertical: CGKeyCode?
    let description: String
}

enum GuideArrowDetector {
    /// Finds the centroid of bright cyan guide-arrow pixels. HUD areas are
    /// excluded so blue skill icons and resource gauges do not affect it.
    static func direction(in image: CGImage) -> MovementDirection? {
        let width = image.width
        let height = image.height
        guard width > 0, height > 0 else { return nil }

        let bytesPerPixel = 4
        let bytesPerRow = width * bytesPerPixel
        var pixels = [UInt8](repeating: 0, count: height * bytesPerRow)
        guard let context = CGContext(
            data: &pixels,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: bytesPerRow,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { return nil }
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))

        var count = 0
        var xTotal = 0
        var yTotal = 0
        let xRange = Int(Double(width) * 0.015)..<Int(Double(width) * 0.985)
        let yRange = Int(Double(height) * 0.10)..<Int(Double(height) * 0.78)

        for y in stride(from: yRange.lowerBound, to: yRange.upperBound, by: 2) {
            for x in stride(from: xRange.lowerBound, to: xRange.upperBound, by: 2) {
                let offset = y * bytesPerRow + x * bytesPerPixel
                let red = Int(pixels[offset])
                let green = Int(pixels[offset + 1])
                let blue = Int(pixels[offset + 2])
                // Guide arrows are saturated cyan-blue with little red.
                if blue > 165, green > 105,
                   blue - red > 75, green - red > 35 {
                    count += 1
                    xTotal += x
                    yTotal += y
                }
            }
        }
        guard count >= 30 else { return nil }

        let x = Double(xTotal) / Double(count) / Double(width)
        let y = Double(yTotal) / Double(count) / Double(height)
        let horizontal: CGKeyCode? = x < 0.40 ? 123 : (x > 0.60 ? 124 : nil)
        let vertical: CGKeyCode? = y < 0.37 ? 126 : (y > 0.63 ? 125 : nil)
        guard horizontal != nil || vertical != nil else { return nil }

        let horizontalName = horizontal == 123 ? "left" : (horizontal == 124 ? "right" : "")
        let verticalName = vertical == 126 ? "up" : (vertical == 125 ? "down" : "")
        return MovementDirection(
            horizontal: horizontal,
            vertical: vertical,
            description: [horizontalName, verticalName].filter { !$0.isEmpty }
                .joined(separator: "+")
        )
    }
}
