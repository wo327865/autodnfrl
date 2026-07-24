import CoreGraphics
import Foundation

final class AbyssSequence {
    private let controller: MacController
    private let dryRun: Bool
    private let timeout: TimeInterval

    init(controller: MacController, dryRun: Bool, timeout: TimeInterval = 18) {
        self.controller = controller
        self.dryRun = dryRun
        self.timeout = timeout
    }

    func run(continueIntoBattle: Bool = false) async throws {
        print("Starting Abyss → Normal Realm setup\(dryRun ? " (dry-run)" : "")")

        try await clickText("委托", state: "main screen")
        if dryRun { return }

        try await clickText("深渊：时空秘境", state: "commission board")
        try await confirmTravel()
        try await continueFromRealmSelection(continueIntoBattle: continueIntoBattle)
    }

    func continueFromRealmSelection(continueIntoBattle: Bool) async throws {
        try await clickText("普通秘境", requiring: "时空秘境", state: "realm selection")
        if try await openPartyPickerIfNeeded() {
            try await selectEligibleCharacters()
        }
        if continueIntoBattle {
            try await BattleSequence(controller: controller).run()
        } else {
            print("Formation is ready. Sequence intentionally stops before 入场.")
        }
    }

    func finishPartySetup() async throws {
        if try await openPartyPickerIfNeeded() {
            try await selectEligibleCharacters()
        }
        print("Formation is ready. Sequence intentionally stops before 入场.")
    }

    func finishOpenCharacterPicker() async throws {
        try await selectEligibleCharacters()
        print("Formation is ready. Sequence intentionally stops before 入场.")
    }

    private func confirmTravel() async throws {
        let deadline = Date().addingTimeInterval(timeout)
        repeat {
            let window = try controller.findWindow()
            let items = try await controller.recognize(window: window)
            if !matches("提示", in: items).isEmpty,
               !matches("时空秘境城镇", in: items).isEmpty,
               !matches("确认", in: items).isEmpty {
                try clickUnique("确认", in: items, window: window)
                return
            }
            if !matches("时空秘境", in: items).isEmpty,
               !matches("普通秘境", in: items).isEmpty {
                print("Travel dialog skipped; realm selection is already open.")
                return
            }
            try await Task.sleep(for: .milliseconds(650))
        } while Date() < deadline
        throw AutomationError.timedOut("travel confirmation or realm selection")
    }

    /// Returns true when the character picker was opened. Existing formations
    /// can persist between runs, in which case there is nothing to change.
    private func openPartyPickerIfNeeded() async throws -> Bool {
        _ = try await waitForAll(["普通秘境"], state: "party setup")
        let deadline = Date().addingTimeInterval(6)
        var consecutiveSingleCharacterFrames = 0

        repeat {
            let window = try controller.findWindow()
            let items = try await controller.recognize(window: window)
            let powerCount = recognizedPartyPowers(in: items).count
            let fatigueCount = matches("100/100", in: items).count

            if powerCount >= 3 || fatigueCount >= 3 {
                print("Detected an existing three-character formation; keeping it.")
                return false
            }

            let slots = matches("可配置角色", in: items)
                .sorted { $0.box.minX < $1.box.minX }
            if let first = slots.first {
                try controller.click(
                    CGPoint(x: first.center.x, y: 0.58),
                    in: window,
                    label: "first available party slot card"
                )
                return true
            }

            if powerCount == 1 || fatigueCount == 1 {
                consecutiveSingleCharacterFrames += 1
            } else {
                consecutiveSingleCharacterFrames = 0
            }
            if consecutiveSingleCharacterFrames >= 2 {
                // Vision often misses the small grey slot label. The party
                // screen and its stable one-character state have been verified;
                // slot locations are fixed within this responsive layout.
                try controller.click(
                    CGPoint(x: 0.49, y: 0.58),
                    in: window,
                    label: "left empty party slot"
                )
                return true
            }
            try await Task.sleep(for: .milliseconds(450))
        } while Date() < deadline

        throw AutomationError.expectedText(
            "stable empty party slot or three occupied characters"
        )
    }

    private func selectEligibleCharacters() async throws {
        var (window, items) = try await waitForAll(
            ["选择冒险团角色", "编队完成"],
            state: "party character picker"
        )

        let alreadySelected = matches("选择完成", in: items).count
        let needed = max(0, 2 - alreadySelected)
        if needed > 0 {
            var candidates = rankedCandidatePoints(items: items)
            let candidateDeadline = Date().addingTimeInterval(6)
            while candidates.isEmpty, Date() < candidateDeadline {
                try await Task.sleep(for: .milliseconds(450))
                window = try controller.findWindow()
                items = try await controller.recognize(window: window)
                candidates = rankedCandidatePoints(items: items)
            }
            guard !candidates.isEmpty else {
                throw AutomationError.partyCandidatesNotFound
            }

            for candidate in candidates.prefix(needed) {
                try controller.click(
                    candidate.point,
                    in: window,
                    label: "eligible character (power \(candidate.power))"
                )
                if !dryRun {
                    try await Task.sleep(for: .milliseconds(450))
                    window = try controller.findWindow()
                    items = try await controller.recognize(window: window)
                }
            }
        }

        if !dryRun {
            let selected = matches("选择完成", in: items).count
            guard selected >= 2 else {
                throw AutomationError.expectedText("two 选择完成 markers")
            }
        }
        try clickUnique("编队完成", in: items, window: window)
    }

    /// Finds combat-power numbers in selectable cards. We derive the card center
    /// from the number's column, so character names are never used.
    private func rankedCandidatePoints(
        items: [OCRItem]
    ) -> [(power: Int, point: CGPoint)] {
        let fatigueItems: [(item: OCRItem, value: Int)] = items.compactMap { item in
            let text = item.normalizedText
            guard text.allSatisfy(\.isNumber),
                  let value = Int(text),
                  value >= 0, value <= 100,
                  item.center.x > 0.14, item.center.x < 0.90,
                  item.center.y > 0.15, item.center.y < 0.68 else {
                return nil
            }
            return (item, value)
        }
        let selectedItems = matches("选择完成", in: items)
        var result: [(Int, CGPoint)] = []

        for item in items {
            guard item.text.contains(",") || item.text.contains("，"),
                  let power = Text.combatPower(from: item.text),
                  item.box.midX > 0.14, item.box.midX < 0.90,
                  item.box.midY > 0.15, item.box.midY < 0.68
            else { continue }

            let column = cardColumn(for: item.center.x)
            let matchingFatigue = fatigueItems.first {
                cardColumn(for: $0.item.center.x) == column &&
                    abs($0.item.center.y - item.center.y) < 0.055
            }
            guard let fatigue = matchingFatigue?.value, fatigue >= 10 else {
                continue
            }
            let alreadySelected = selectedItems.contains {
                cardColumn(for: $0.center.x) == column &&
                    abs($0.center.y - item.center.y) < 0.085
            }
            guard !alreadySelected else { continue }

            // Names and row counts may change. Power and fatigue observations
            // identify each eligible card; only column centers are structural.
            let cardX: CGFloat = [0.267, 0.511, 0.755][column]
            result.append((power, CGPoint(x: cardX, y: item.box.midY + 0.035)))
        }

        // De-duplicate OCR alternatives from the same card and rank by power.
        var byCard: [String: (Int, CGPoint)] = [:]
        for candidate in result {
            let column = Int(candidate.1.x * 10)
            let row = Int(candidate.1.y * 10)
            let key = "\(column):\(row)"
            if candidate.0 > (byCard[key]?.0 ?? -1) {
                byCard[key] = candidate
            }
        }
        return byCard.values.sorted { $0.0 > $1.0 }
    }

    private func cardColumn(for x: CGFloat) -> Int {
        if x < 0.39 { return 0 }
        if x < 0.65 { return 1 }
        return 2
    }

    private func recognizedPartyPowers(in items: [OCRItem]) -> [Int] {
        items.compactMap { item in
            guard item.box.midX > 0.40,
                  item.box.midY > 0.22, item.box.midY < 0.45 else { return nil }
            return Text.combatPower(from: item.text)
        }
    }

    private func clickText(
        _ target: String,
        requiring required: String? = nil,
        state: String
    ) async throws {
        let requiredTexts = [target] + (required.map { [$0] } ?? [])
        let (window, items) = try await waitForAll(requiredTexts, state: state)
        try clickUnique(target, in: items, window: window)
    }

    private func clickUnique(
        _ target: String,
        in items: [OCRItem],
        window: WindowTarget
    ) throws {
        let found = matches(target, in: items)
        guard found.count == 1 else {
            if found.isEmpty { throw AutomationError.expectedText(target) }
            throw AutomationError.ambiguousText(target, found.count)
        }
        try controller.click(found[0].center, in: window, label: target)
    }

    private func matches(_ target: String, in items: [OCRItem]) -> [OCRItem] {
        let needle = Text.normalized(target)
        return items.filter { item in
            if item.normalizedText.contains(needle) {
                return true
            }
            // On very bright town themes Vision sometimes reads 委托 as 委31.
            // Limit the fallback to the right-side commission-button region.
            return target == "委托" &&
                item.normalizedText.contains("委") &&
                item.center.x > 0.90 &&
                item.center.y > 0.20 && item.center.y < 0.36
        }
    }

    private func waitForAll(
        _ texts: [String],
        state: String
    ) async throws -> (WindowTarget, [OCRItem]) {
        let deadline = Date().addingTimeInterval(timeout)
        repeat {
            let window = try controller.findWindow()
            let items = try await controller.recognize(window: window)
            let allPresent = texts.allSatisfy { !matches($0, in: items).isEmpty }
            if allPresent {
                print("Detected \(state)")
                return (window, items)
            }
            if dryRun {
                let visible = items.map(\.text).joined(separator: " | ")
                print("Visible OCR: \(visible)")
                throw AutomationError.expectedText(texts.joined(separator: ", "))
            }
            try await Task.sleep(for: .milliseconds(700))
        } while Date() < deadline
        throw AutomationError.timedOut(state)
    }
}
