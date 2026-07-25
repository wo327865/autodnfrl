import CoreGraphics
import Foundation

enum ConfirmationButtonDetector {
    /// Finds the large gold confirmation button in a centered game dialog.
    /// This deliberately uses the rendered button rather than OCR because
    /// Vision can merge the adjacent 取消 / 确认 labels into one text region.
    static func confirmationPoint(in image: CGImage) -> CGPoint? {
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

        // Sample a coarse grid to locate the broad gold rectangle; this keeps
        // the connected-component search small even for Retina captures.
        let step = 3
        let gridWidth = width / step
        let gridHeight = height / step
        var mask = [Bool](repeating: false, count: gridWidth * gridHeight)
        for gy in 0..<gridHeight {
            for gx in 0..<gridWidth {
                let x = gx * step
                let y = gy * step
                let offset = y * bytesPerRow + x * bytesPerPixel
                let red = Int(pixels[offset])
                let green = Int(pixels[offset + 1])
                let blue = Int(pixels[offset + 2])
                // Confirmation is yellow-gold; cancel is visibly more orange.
                mask[gy * gridWidth + gx] = red > 170 && green > 110 &&
                    blue < 105 && red - green < 105
            }
        }

        var visited = [Bool](repeating: false, count: mask.count)
        var best: (area: Int, centerX: Double, centerY: Double)?
        for start in mask.indices where mask[start] && !visited[start] {
            var queue = [start]
            visited[start] = true
            var index = 0
            var area = 0
            var xSum = 0
            var ySum = 0

            while index < queue.count {
                let current = queue[index]
                index += 1
                let x = current % gridWidth
                let y = current / gridWidth
                area += 1
                xSum += x
                ySum += y
                for (nx, ny) in [(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)] {
                    guard nx >= 0, nx < gridWidth, ny >= 0, ny < gridHeight else { continue }
                    let neighbor = ny * gridWidth + nx
                    if mask[neighbor] && !visited[neighbor] {
                        visited[neighbor] = true
                        queue.append(neighbor)
                    }
                }
            }

            guard area > 220 else { continue }
            let centerX = Double(xSum) / Double(area) / Double(gridWidth)
            let centerY = Double(ySum) / Double(area) / Double(gridHeight)
            // Dialog action buttons sit in the central lower area. The desired
            // button is always the right-hand gold component.
            guard centerX > 0.50, centerX < 0.78,
                  centerY > 0.20, centerY < 0.62 else { continue }
            if best == nil || centerX > best!.centerX || area > best!.area {
                best = (area, centerX, centerY)
            }
        }

        guard let best else { return nil }
        // Bitmap-context coordinates have the same bottom-left orientation as
        // Vision normalized coordinates.
        return CGPoint(x: best.centerX, y: best.centerY)
    }
}
