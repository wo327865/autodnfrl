from __future__ import annotations

import argparse

from .macos import MacClient
from .workflow import AutoDNF


def main() -> None:
    parser = argparse.ArgumentParser(description="DNF PlayCover automation")
    parser.add_argument("command", choices=["scan", "run", "realm", "party", "battle"])
    parser.add_argument("--execute", action="store_true", help="allow clicks and key presses")
    parser.add_argument("--battle", action="store_true", help="continue into the dungeon after party formation")
    parser.add_argument("--debug", action="store_true", help="print OCR-derived party-card detection details")
    parser.add_argument("--window", default="地下城与勇士")
    args = parser.parse_args()

    client = MacClient(args.window, args.execute)
    flow = AutoDNF(client, debug=args.debug)
    if args.command == "scan":
        window = client.find_window()
        for box in sorted(client.ocr(window), key=lambda item: -item.center[1]):
            print(f"({box.center[0]:.3f}, {box.center[1]:.3f}) {box.text}")
    elif args.command == "run":
        if args.battle:
            flow.run_all_characters()
        else:
            flow.run_to_party(battle=False)
    elif args.command == "realm":
        flow.continue_from_realm_selection(battle=args.battle)
    elif args.command == "party":
        flow.configure_party()
        if args.battle:
            flow.run_battle(start_by_entering=True)
    else:
        flow.run_battle(start_by_entering=False)


if __name__ == "__main__":
    main()
