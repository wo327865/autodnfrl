# AutoDNF

A native macOS proof-of-concept for navigating the captured DNF Mobile
“时空秘境” flow in a PlayCover window. It uses Apple Vision OCR, so button
positions and character names are not hard-coded.

Current sequence:

1. `委托`
2. `深渊：时空秘境`
3. Validate the destination dialog, then `确认`
4. `普通秘境`
5. Open `可配置角色`
6. Select up to two eligible, unlocked characters, preferring higher combat
   power
7. `编队完成`
8. With `--battle`, enter the dungeon, advance through cleared rooms, collect
   boss drops by clicking their recognized labels, and retry while possible

Without `--battle`, it intentionally stops before `入场`. The battle loop is
experimental and may consume entry materials and fatigue, so supervise initial
runs after game updates.

## Permissions

The terminal application running the tool needs:

- **Privacy & Security → Screen Recording** (capture the PlayCover window)
- **Privacy & Security → Accessibility** (send clicks)

Restart the terminal after granting a permission.

## Run

Keep the PlayCover window visible and start on the logged-in role’s main screen.

```sh
swift run autodnf scan
```

`scan` performs no clicks and prints OCR results. The automation is also a
dry-run unless clicking is explicitly enabled:

```sh
swift run autodnf
swift run autodnf --execute
```

To enter and repeat the dungeon until `再次挑战` no longer starts a new run:

```sh
swift run autodnf --execute --battle
```

To resume safely when already on the party setup screen:

```sh
swift run autodnf party --execute
```

For development, an already-open character picker can be resumed with:

```sh
swift run autodnf picker --execute
```

If the window title differs, provide an owner/title fragment:

```sh
swift run autodnf --execute --window PlayCover
```

Every transition is checked by expected Chinese screen text. If recognition is
missing or ambiguous, the tool stops instead of making a guessed click.

## Development

```sh
swift run autodnf self-test
```
