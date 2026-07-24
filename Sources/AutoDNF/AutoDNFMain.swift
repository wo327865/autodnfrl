import Foundation
import Darwin

@main
struct AutoDNF {
    static func main() async {
        let arguments = Array(CommandLine.arguments.dropFirst())
        let execute = arguments.contains("--execute")
        let scan = arguments.contains("scan")
        let partyOnly = arguments.contains("party")
        let pickerOnly = arguments.contains("picker")
        let selfTest = arguments.contains("self-test")
        let battle = arguments.contains("--battle")
        let realmOnly = arguments.contains("realm")
        let combatOnly = arguments.contains("combat")
        let pickupOnly = arguments.contains("pickup")
        let hint = option("--window", in: arguments) ?? "地下城与勇士"
        let controller = MacController(windowHint: hint, dryRun: !execute)

        do {
            if selfTest {
                try runSelfTests()
                print("All built-in tests passed.")
                return
            }
            if scan {
                let window = try controller.findWindow()
                print("Window: \(window.owner) / \(window.title), id=\(window.id)")
                let items = try await controller.recognize(window: window)
                for item in items.sorted(by: { $0.box.midY > $1.box.midY }) {
                    print(String(format: "(%.3f, %.3f) %@", item.center.x, item.center.y, item.text))
                }
                return
            }

            if !execute {
                print("Safety: dry-run is the default. Pass --execute to allow clicks.")
            }
            let sequence = AbyssSequence(controller: controller, dryRun: !execute)
            if pickupOnly {
                for _ in 0..<18 {
                    try await controller.holdKey(
                        code: 125,
                        for: .milliseconds(260),
                        label: "down toward visible drops"
                    )
                    try await controller.tapKey(code: 7, label: "X pickup test")
                    try await Task.sleep(for: .milliseconds(120))
                }
            } else if combatOnly {
                try await BattleSequence(controller: controller).run(startByEntering: false)
            } else if realmOnly {
                try await sequence.continueFromRealmSelection(continueIntoBattle: battle)
            } else if pickerOnly {
                try await sequence.finishOpenCharacterPicker()
                if battle {
                    try await BattleSequence(controller: controller).run()
                }
            } else if partyOnly {
                try await sequence.finishPartySetup()
                if battle {
                    try await BattleSequence(controller: controller).run()
                }
            } else {
                try await sequence.run(continueIntoBattle: battle)
            }
        } catch {
            FileHandle.standardError.write(
                Data("autodnf: \(error.localizedDescription)\n".utf8)
            )
            Darwin.exit(1)
        }
    }

    private static func option(_ name: String, in arguments: [String]) -> String? {
        guard let index = arguments.firstIndex(of: name),
              arguments.indices.contains(index + 1) else { return nil }
        return arguments[index + 1]
    }

    private static func runSelfTests() throws {
        guard Text.normalized(" 深渊: 时空秘境\n") == "深渊：时空秘境",
              Text.combatPower(from: "134,513") == 134_513,
              Text.combatPower(from: "99，347") == 99_347,
              Text.combatPower(from: "W 134,513") == 134_513,
              Text.combatPower(from: "X123,882") == 123_882,
              Text.combatPower(from: "100") == nil,
              Text.combatPower(from: "等级80") == nil else {
            throw AutomationError.captureFailed("built-in parsing test failed")
        }
    }
}
