import CoreGraphics
import Foundation

struct OCRItem: Sendable {
    let text: String
    /// Vision coordinates: normalized, with the origin at bottom-left.
    let box: CGRect

    var normalizedText: String { Text.normalized(text) }
    var center: CGPoint { CGPoint(x: box.midX, y: box.midY) }
}

enum Text {
    static func normalized(_ value: String) -> String {
        value
            .replacingOccurrences(of: " ", with: "")
            .replacingOccurrences(of: "\n", with: "")
            .replacingOccurrences(of: ":", with: "：")
            .replacingOccurrences(of: "﹕", with: "：")
            .replacingOccurrences(of: "，", with: ",")
    }

    static func combatPower(from value: String) -> Int? {
        // Decorative stat icons are often recognized as W/X prefixes. Prefer
        // a grouped number such as 134,513 anywhere in the OCR observation.
        let normalized = value.replacingOccurrences(of: "，", with: ",")
        let patterns = [
            #"[0-9]+(?:,[0-9]{3})+"#,
            #"[0-9]{5,}"#
        ]
        for pattern in patterns {
            guard let expression = try? NSRegularExpression(pattern: pattern) else {
                continue
            }
            let range = NSRange(normalized.startIndex..., in: normalized)
            guard let match = expression.firstMatch(
                in: normalized,
                range: range
            ), let swiftRange = Range(match.range, in: normalized) else {
                continue
            }
            let digits = normalized[swiftRange].replacingOccurrences(of: ",", with: "")
            if let number = Int(digits) {
                return number
            }
        }
        return nil
    }
}

struct WindowTarget: Sendable {
    let id: CGWindowID
    let pid: pid_t
    let owner: String
    let title: String
    let bounds: CGRect
}

enum AutomationError: LocalizedError {
    case windowNotFound(String)
    case captureFailed(String)
    case expectedText(String)
    case ambiguousText(String, Int)
    case partyCandidatesNotFound
    case timedOut(String)

    var errorDescription: String? {
        switch self {
        case .windowNotFound(let hint):
            "No visible PlayCover/DNF window matched “\(hint)”."
        case .captureFailed(let reason):
            "Window capture failed: \(reason)"
        case .expectedText(let text):
            "Expected screen text was not found: \(text)"
        case .ambiguousText(let text, let count):
            "Expected one match for “\(text)”, but found \(count)."
        case .partyCandidatesNotFound:
            "Could not identify any eligible party characters."
        case .timedOut(let state):
            "Timed out waiting for \(state)."
        }
    }
}
