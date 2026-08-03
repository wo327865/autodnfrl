from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .macos import GlobalStopShortcut, MacClient
from .template_matcher import FixedTemplateMatcher
from .vision_fallback import GeminiVisionFallback, VisionFallbackError
from .workflow import AutoDNF, exact, find


def main() -> None:
    parser = argparse.ArgumentParser(description="DNF PlayCover automation")
    parser.add_argument(
        "command",
        choices=["scan", "capture", "run", "realm", "party", "battle", "maintenance", "autoclick"],
    )
    parser.add_argument("--execute", action="store_true", help="allow clicks and key presses")
    parser.add_argument("--battle", action="store_true", help="continue into the dungeon after party formation")
    parser.add_argument("--debug", action="store_true", help="print OCR-derived party-card detection details")
    parser.add_argument(
        "--vision-fallback",
        action="store_true",
        help="ask Gemini for advice when a known non-dungeon state times out",
    )
    parser.add_argument(
        "--vision-auto-act",
        action="store_true",
        help="allow locally validated safe AI actions (requires --vision-fallback and --execute)",
    )
    parser.add_argument("--window", default="地下城与勇士")
    parser.add_argument("--count", type=int, default=1, help="frames to save with the capture command")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between captured frames")
    parser.add_argument(
        "--click-interval",
        type=float,
        default=5.0,
        help="seconds between autoclick attempts (default: 5)",
    )
    parser.add_argument("--tag", default="frame", help="scenario tag used in captured frame names")
    parser.add_argument("--output", default="dataset/images", help="directory for captured detector-training images")
    parser.add_argument(
        "--template-manifest",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "templates/dismantle/manifest.json",
        help="fixed-position UI template manifest",
    )
    args = parser.parse_args()
    if args.vision_auto_act and not args.vision_fallback:
        parser.error("--vision-auto-act requires --vision-fallback")

    client = MacClient(args.window, args.execute)
    try:
        vision_fallback = (
            GeminiVisionFallback.from_env() if args.vision_fallback else None
        )
    except VisionFallbackError as error:
        parser.error(str(error))
    if args.debug:
        if vision_fallback is None:
            print(
                "AI vision fallback is disabled; use --vision-fallback "
                "(and set GEMINI_API_KEY) to enable advice"
            )
        else:
            action_mode = "safe auto-actions enabled" if args.vision_auto_act else "advice only"
            print(
                f"AI vision fallback enabled with {vision_fallback.model} "
                f"({action_mode})"
            )
    template_matcher = None
    if args.template_manifest.is_file():
        try:
            template_matcher = FixedTemplateMatcher.load(args.template_manifest)
        except ValueError as error:
            parser.error(str(error))
    flow = AutoDNF(
        client,
        debug=args.debug,
        vision_fallback=vision_fallback,
        vision_auto_act=args.vision_auto_act,
        template_matcher=template_matcher,
    )
    shortcut = GlobalStopShortcut()
    shortcut.start()
    print("Press Control+T at any time to stop AutoDNF.")
    try:
        if args.command == "scan":
            window = client.find_window()
            for box in sorted(client.ocr(window), key=lambda item: -item.center[1]):
                print(f"({box.center[0]:.3f}, {box.center[1]:.3f}) {box.text}")
        elif args.command == "capture":
            if args.count < 1:
                parser.error("--count must be at least 1")
            output = Path(args.output)
            manifest = output.parent / "manifest.jsonl"
            for index in range(args.count):
                window = client.find_window()
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
                image_path = output / f"{args.tag}_{stamp}.png"
                client.capture_png(window, image_path)
                record = {
                    "image": str(image_path),
                    "tag": args.tag,
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "window": {"width": window.width, "height": window.height},
                }
                manifest.parent.mkdir(parents=True, exist_ok=True)
                with manifest.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(f"Captured {image_path}")
                if index + 1 < args.count:
                    time.sleep(max(0.0, args.interval))
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
        elif args.command == "maintenance":
            flow.run_mail_maintenance_all()
        elif args.command == "autoclick":
            interval = max(0.2, args.click_interval)
            print(f"Clicking exact 启动转盘 every {interval:g} seconds. Press Control+T to stop.")
            while True:
                window = client.find_window()
                boxes = client.ocr(window)
                buttons = exact("启动转盘", boxes) or find("启动转盘", boxes)
                if len(buttons) == 1:
                    client.click(window, buttons[0].center, "启动转盘")
                elif not buttons:
                    print("启动转盘 is not currently visible; waiting")
                else:
                    print(f"Found {len(buttons)} 启动转盘 candidates; not clicking ambiguously")
                time.sleep(interval)
        else:
            flow.run_battle(start_by_entering=False)
    except KeyboardInterrupt:
        if shortcut.triggered:
            print("\nControl+T pressed. AutoDNF stopped.")
        else:
            print("\nAutoDNF interrupted.")
    finally:
        shortcut.close()


if __name__ == "__main__":
    main()
