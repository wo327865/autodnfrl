import CoreGraphics
import Foundation

final class BattleSequence {
    private let controller: MacController
    private let leftArrow: CGKeyCode = 123
    private let rightArrow: CGKeyCode = 124
    private let downArrow: CGKeyCode = 125
    private let upArrow: CGKeyCode = 126
    private let pickupX: CGKeyCode = 7

    init(controller: MacController) {
        self.controller = controller
    }

    func run(startByEntering: Bool = true) async throws {
        if startByEntering {
            try await clickExpected("入场", state: "ready formation")
        }
        let deadline = Date().addingTimeInterval(60 * 60)

        while Date() < deadline {
            let window = try controller.findWindow()
            let items = try await controller.recognize(window: window)

            if contains("委托", in: items) || contains("返回城镇", in: items) {
                print("Town screen detected. Battle loop complete.")
                return
            } else if contains("入场材料", in: items), contains("入场", in: items) {
                try clickUnique("入场", items: items, window: window)
                try await Task.sleep(for: .seconds(1))
            } else if contains("使用角色金库和冒险团金库物品", in: items) {
                guard contains("确定要使用吗", in: items) else {
                    throw AutomationError.expectedText("确定要使用吗")
                }
                try clickUnique("确认", items: items, window: window)
                try await Task.sleep(for: .seconds(2))
            } else if contains("再次挑战", in: items),
                      contains("领奖结算", in: items) {
                print("Boss completion detected; collecting drops.")
                try await collectDrops()
                let retry = try await retryState()
                if retry == .started {
                    print("Next challenge started.")
                } else if retry == .noEnergy {
                    let exitWindow = try controller.findWindow()
                    let exitItems = try await controller.recognize(window: exitWindow)
                    try clickUnique("领奖结算", items: exitItems, window: exitWindow)
                    print("No energy remains. Exited settlement.")
                    return
                } else {
                    throw AutomationError.expectedText(
                        "retry transition while fatigue remains"
                    )
                }
            } else if contains("区域已清理", in: items) {
                try await controller.holdKey(
                    code: rightArrow,
                    for: .seconds(2),
                    label: "right arrow toward next room"
                )
                try await Task.sleep(for: .milliseconds(500))
            } else {
                // Some dungeon themes show only a graphical blue exit arrow,
                // with no OCR-readable clear banner. Bounded pulses keep the
                // main character advancing; companions handle nearby combat.
                let partyFatigueCount = items.filter {
                    $0.normalizedText.contains("/100")
                }.count
                if contains("秘境：", in: items) || partyFatigueCount >= 3 {
                    try await controller.holdKey(
                        code: rightArrow,
                        for: .milliseconds(850),
                        label: "right arrow dungeon advance"
                    )
                    try await Task.sleep(for: .milliseconds(450))
                } else {
                    try await Task.sleep(for: .milliseconds(700))
                }
            }
        }
        throw AutomationError.timedOut("one-hour battle safety limit")
    }

    private func collectDrops() async throws {
        try await followRewardGuideArrows()

        // The game supports clicking an item label directly. Restrict clicks to
        // the lower-middle world area so HUD controls and settlement buttons
        // can never be mistaken for drops.
        var emptyFrames = 0
        let deadline = Date().addingTimeInterval(45)
        while Date() < deadline, emptyFrames < 2 {
            let window = try controller.findWindow()
            let items = try await controller.recognize(window: window)
            let drops = dropLabelCandidates(in: items)

            if drops.isEmpty {
                emptyFrames += 1
                try await Task.sleep(for: .milliseconds(700))
                continue
            }

            emptyFrames = 0
            // Prefer the lowest visible label; it is least likely to be
            // occluded by another drop label.
            guard let target = drops.min(by: { $0.box.midY < $1.box.midY }) else {
                continue
            }
            try controller.click(
                target.center,
                in: window,
                label: "dropped item: \(target.text)"
            )
            try await Task.sleep(for: .milliseconds(900))
        }
        guard emptyFrames >= 2 else {
            throw AutomationError.timedOut("click-collecting boss drops")
        }
    }

    private func followRewardGuideArrows() async throws {
        var missingFrames = 0
        let deadline = Date().addingTimeInterval(30)
        while Date() < deadline, missingFrames < 2 {
            let window = try controller.findWindow()
            let image = try controller.captureWindow(window)
            guard let direction = GuideArrowDetector.direction(in: image) else {
                missingFrames += 1
                try await Task.sleep(for: .milliseconds(450))
                continue
            }
            missingFrames = 0
            let keys = [direction.horizontal, direction.vertical].compactMap { $0 }
            try await controller.holdKeys(
                keys,
                for: .milliseconds(500),
                label: "reward guide \(direction.description)"
            )
            try await Task.sleep(for: .milliseconds(250))
        }
        guard missingFrames >= 2 else {
            throw AutomationError.timedOut("following reward guidance arrows")
        }
        print("Reward guidance arrows cleared; switching to item clicks.")
    }

    private func dropLabelCandidates(in items: [OCRItem]) -> [OCRItem] {
        items.filter { item in
            let point = item.center
            guard point.x > 0.20, point.x < 0.78,
                  point.y > 0.15, point.y < 0.42 else {
                return false
            }
            let text = item.normalizedText
            return text.count >= 2 &&
                !text.contains("等级") &&
                !text.contains("疲劳") &&
                !text.contains("经验值")
        }
    }

    private enum RetryState {
        case started
        case noEnergy
        case stuck
    }

    private func retryState() async throws -> RetryState {
        var window = try controller.findWindow()
        var items = try await controller.recognize(window: window)
        try clickUnique("再次挑战", items: items, window: window)
        let deadline = Date().addingTimeInterval(15)
        repeat {
            try await Task.sleep(for: .milliseconds(700))
            window = try controller.findWindow()
            items = try await controller.recognize(window: window)
            if !contains("再次挑战", in: items) || !contains("领奖结算", in: items) {
                return .started
            }
        } while Date() < deadline

        guard contains("再次挑战", in: items), contains("领奖结算", in: items) else {
            return .started
        }
        let fatigue = items.compactMap { item -> Int? in
            let text = item.normalizedText
            guard let slash = text.range(of: "/100") else { return nil }
            return Int(text[..<slash.lowerBound].filter(\.isNumber))
        }
        if let minimum = fatigue.min(), minimum < 10 {
            return .noEnergy
        }
        return .stuck
    }

    private func clickExpected(_ text: String, state: String) async throws {
        let deadline = Date().addingTimeInterval(15)
        repeat {
            let window = try controller.findWindow()
            let items = try await controller.recognize(window: window)
            if contains(text, in: items) {
                try clickUnique(text, items: items, window: window)
                return
            }
            try await Task.sleep(for: .milliseconds(600))
        } while Date() < deadline
        throw AutomationError.timedOut(state)
    }

    private func clickUnique(
        _ text: String,
        items: [OCRItem],
        window: WindowTarget
    ) throws {
        let target = Text.normalized(text)
        let exact = items.filter { $0.normalizedText == target }
        let found = exact.isEmpty
            ? items.filter { $0.normalizedText.contains(target) }
            : exact
        guard found.count == 1 else {
            if found.isEmpty { throw AutomationError.expectedText(text) }
            throw AutomationError.ambiguousText(text, found.count)
        }
        try controller.click(found[0].center, in: window, label: text)
    }

    private func contains(_ text: String, in items: [OCRItem]) -> Bool {
        let target = Text.normalized(text)
        return items.contains { $0.normalizedText.contains(target) }
    }
}
