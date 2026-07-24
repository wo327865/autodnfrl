import AppKit
import CoreGraphics
import Foundation
@preconcurrency import Vision

final class MacController {
    private let windowHint: String
    private let dryRun: Bool

    init(windowHint: String, dryRun: Bool) {
        self.windowHint = windowHint
        self.dryRun = dryRun
    }

    func findWindow() throws -> WindowTarget {
        guard let raw = CGWindowListCopyWindowInfo(
            [.optionOnScreenOnly, .excludeDesktopElements],
            kCGNullWindowID
        ) as? [[String: Any]] else {
            throw AutomationError.windowNotFound(windowHint)
        }

        let hint = windowHint.lowercased()
        let candidates = raw.compactMap { info -> WindowTarget? in
            guard let id = info[kCGWindowNumber as String] as? NSNumber,
                  let pid = info[kCGWindowOwnerPID as String] as? NSNumber,
                  let owner = info[kCGWindowOwnerName as String] as? String,
                  let rawBounds = info[kCGWindowBounds as String] else { return nil }
            // CGWindowList guarantees that kCGWindowBounds is a CFDictionary.
            let boundsDictionary = rawBounds as! CFDictionary
            guard let bounds = CGRect(dictionaryRepresentation: boundsDictionary),
                  bounds.width > 500, bounds.height > 300 else { return nil }
            let title = info[kCGWindowName as String] as? String ?? ""
            let searchable = "\(owner) \(title)".lowercased()
            guard searchable.contains(hint) ||
                    searchable.contains("playcover") ||
                    title.contains("地下城与勇士") else { return nil }
            return WindowTarget(
                id: CGWindowID(id.uint32Value),
                pid: pid_t(pid.int32Value),
                owner: owner,
                title: title,
                bounds: bounds
            )
        }

        guard let best = candidates.max(by: {
            $0.bounds.width * $0.bounds.height < $1.bounds.width * $1.bounds.height
        }) else {
            throw AutomationError.windowNotFound(windowHint)
        }
        return best
    }

    func recognize(window: WindowTarget) async throws -> [OCRItem] {
        let image = try captureWindow(window)
        return try await withCheckedThrowingContinuation { continuation in
            let request = VNRecognizeTextRequest { request, error in
                if let error {
                    continuation.resume(throwing: error)
                    return
                }
                let items = (request.results as? [VNRecognizedTextObservation] ?? [])
                    .flatMap { observation -> [OCRItem] in
                        guard let candidate = observation.topCandidates(1).first else {
                            return []
                        }
                        var recognized = [
                            OCRItem(text: candidate.string, box: observation.boundingBox)
                        ]

                        // Vision can merge adjacent buttons into one line, for
                        // example "取消 确认". Preserve the full line for state
                        // matching, but also retain precise geometry for every
                        // whitespace-delimited token so clicks land on the
                        // intended button rather than between buttons.
                        let tokens = candidate.string.split(whereSeparator: \.isWhitespace)
                        if tokens.count > 1 {
                            var searchStart = candidate.string.startIndex
                            for tokenSlice in tokens {
                                let token = String(tokenSlice)
                                guard let range = candidate.string.range(
                                    of: token,
                                    range: searchStart..<candidate.string.endIndex
                                ) else { continue }
                                searchStart = range.upperBound
                                if let rectangle = try? candidate.boundingBox(for: range) {
                                    recognized.append(
                                        OCRItem(text: token, box: rectangle.boundingBox)
                                    )
                                }
                            }
                        }
                        return recognized
                    }
                continuation.resume(returning: items)
            }
            request.recognitionLevel = .accurate
            request.recognitionLanguages = ["zh-Hans", "en-US"]
            request.usesLanguageCorrection = true
            request.minimumTextHeight = 0.012

            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    try VNImageRequestHandler(cgImage: image).perform([request])
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    func click(_ normalizedVisionPoint: CGPoint, in window: WindowTarget, label: String) throws {
        // CGWindow bounds use the macOS global coordinate system (origin top-left);
        // Vision uses bottom-left normalized coordinates.
        let point = CGPoint(
            x: window.bounds.minX + normalizedVisionPoint.x * window.bounds.width,
            y: window.bounds.minY + (1 - normalizedVisionPoint.y) * window.bounds.height
        )
        print("\(dryRun ? "[dry-run] would click" : "click") \(label) at " +
              "(\(Int(point.x)), \(Int(point.y)))")
        guard !dryRun else { return }

        NSRunningApplication(processIdentifier: window.pid)?
            .activate(options: [.activateIgnoringOtherApps])
        usleep(120_000)
        guard let down = CGEvent(
            mouseEventSource: nil,
            mouseType: .leftMouseDown,
            mouseCursorPosition: point,
            mouseButton: .left
        ), let up = CGEvent(
            mouseEventSource: nil,
            mouseType: .leftMouseUp,
            mouseCursorPosition: point,
            mouseButton: .left
        ) else {
            throw AutomationError.captureFailed("could not create mouse event")
        }
        down.post(tap: .cghidEventTap)
        usleep(70_000)
        up.post(tap: .cghidEventTap)
    }

    func holdKey(code: CGKeyCode, for duration: Duration, label: String) async throws {
        print("hold \(label)")
        guard !dryRun else { return }
        try await focusGame()
        guard let down = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true),
              let up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false)
        else {
            throw AutomationError.captureFailed("could not create keyboard event")
        }
        down.post(tap: .cghidEventTap)
        do {
            try await Task.sleep(for: duration)
            up.post(tap: .cghidEventTap)
        } catch {
            up.post(tap: .cghidEventTap)
            throw error
        }
    }

    func tapKey(code: CGKeyCode, label: String) async throws {
        print("tap \(label)")
        guard !dryRun else { return }
        try await focusGame()
        guard let down = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true),
              let up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false)
        else {
            throw AutomationError.captureFailed("could not create keyboard event")
        }
        down.post(tap: .cghidEventTap)
        try await Task.sleep(for: .milliseconds(220))
        up.post(tap: .cghidEventTap)
    }

    func holdKeys(
        _ codes: [CGKeyCode],
        for duration: Duration,
        label: String
    ) async throws {
        print("hold \(label)")
        guard !dryRun else { return }
        try await focusGame()
        let events = codes.compactMap {
            (
                CGEvent(keyboardEventSource: nil, virtualKey: $0, keyDown: true),
                CGEvent(keyboardEventSource: nil, virtualKey: $0, keyDown: false)
            )
        }
        guard events.count == codes.count else {
            throw AutomationError.captureFailed("could not create keyboard events")
        }
        events.forEach { $0.0?.post(tap: .cghidEventTap) }
        do {
            try await Task.sleep(for: duration)
            events.reversed().forEach { $0.1?.post(tap: .cghidEventTap) }
        } catch {
            events.reversed().forEach { $0.1?.post(tap: .cghidEventTap) }
            throw error
        }
    }

    private func focusGame() async throws {
        let window = try findWindow()
        guard let application = NSRunningApplication(processIdentifier: window.pid) else {
            throw AutomationError.windowNotFound(windowHint)
        }
        application.activate(options: [.activateIgnoringOtherApps])
        try await Task.sleep(for: .milliseconds(120))
    }

    func captureWindow(_ window: WindowTarget) throws -> CGImage {
        let output = FileManager.default.temporaryDirectory
            .appendingPathComponent("autodnf-\(UUID().uuidString).png")
        defer { try? FileManager.default.removeItem(at: output) }

        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        task.arguments = ["-x", "-o", "-l", String(window.id), output.path]
        let errorPipe = Pipe()
        task.standardError = errorPipe
        try task.run()
        task.waitUntilExit()
        guard task.terminationStatus == 0,
              let image = NSImage(contentsOf: output),
              let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
            let data = errorPipe.fileHandleForReading.readDataToEndOfFile()
            let message = String(data: data, encoding: .utf8) ?? "unknown error"
            throw AutomationError.captureFailed(
                "\(message) Grant Screen Recording permission to the terminal running autodnf."
            )
        }
        return cgImage
    }
}
