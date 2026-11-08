# Lumina: spec (v1, hackathon scope)

Lumina lets you ask Hermes about your Apple Photos library: "find my passport", "photos of Mom at the beach", "make an album of our Goa trip". It reads the data Apple Photos has already computed on-device (people, places, scene labels, text found in images) through `osxphotos` (MIT). macOS only. No network, no API keys.

Plugin id / folder name: `lumina`. One installable folder:

```
lumina/
├── plugin.yaml
├── __init__.py            # register(ctx): tools + pre_tool_call guard + skill
├── photos.py              # all osxphotos access (pure functions, testable)
├── skills/lumina/SKILL.md # tells the agent when to call tools and to emit ::lumina-photos{}
├── dashboard/
│   ├── manifest.json      # {"name": "lumina", "api": "plugin_api.py"}
│   └── plugin_api.py      # FastAPI router, mounted at /api/plugins/lumina/
├── desktop/plugin.js      # Desktop SDK half (plain ESM, no JSX)
└── tests/
```

## Lane A (backend, Python)

### Tools (registered with ctx.register_tool)

All tools return a JSON string. Errors: `{"error": "<code>", "message": "<human text>"}` with codes
`no_access` (macOS Full Disk Access missing: message explains System Settings → Privacy & Security → Full Disk Access → enable Hermes / the terminal),
`no_library`, `not_found`, `bad_args`, `photos_app_error`.

1. `lumina_search`
   - args (all optional, at least one required): `text` (free text: matched against detected text, labels, title, description, keywords, place), `person` (string, named face), `place` (string), `label` (scene label, e.g. "beach"), `date_from`, `date_to` (ISO dates), `media` ("photo" | "video" | "screenshot" | "any", default "any"), `limit` (int, default 24, max 200).
   - returns `{"count": N, "total_matches": M, "photos": [Photo, ...]}`.
   - `Photo` = `{"uuid", "date" (ISO), "title", "place" (string or null), "persons" ([str]), "labels" ([str], max 8), "text_snippet" (str or null, max 80 chars), "is_local" (bool: original or derivative on disk), "is_screenshot", "is_video"}`.
   - Newest first. Skip hidden and already-deleted photos. Never return file paths to the model.
2. `lumina_create_album`
   - args: `name` (str, required), `uuids` ([str], required, max 500).
   - creates (or adds to an existing same-name) album in Apple Photos via PhotoScript. Returns `{"album": name, "added": N}`.
3. `lumina_library_info`
   - no args. Returns `{"photos": N, "videos": N, "people": [top 20 named persons], "library_path_present": bool}`. Used for onboarding and permission checks.

Cleanup/delete is OUT of v1. Do not register any delete tool.

### Thumbnail backend (dashboard/plugin_api.py)

- `GET /thumbs?uuids=u1,u2,...&size=s|m` (max 60 uuids) → `{"items": [{"uuid", "date", "title", "place", "data_url": "data:image/jpeg;base64,..." | null}]}`.
  - Use Photos' own derivative JPEGs (`photo.path_derived`/`path_derivatives`) when present; else downscale the local original with Pillow if available; else `data_url: null`.
  - size s = longest side 256 px, m = 768 px. Cache encoded results in memory (LRU 500).
- `GET /open?uuid=...` → opens that photo in Apple Photos (PhotoScript `spotlight`), returns `{"ok": true}`.
- `GET /status` → same as `lumina_library_info` plus `{"access": "ok" | "no_access"}`.

### Rules

- Open the library once (osxphotos `PhotosDB()`) and reuse it; reload if the library file mtime changes. Opening is slow on big libraries: do it lazily on first call, not in `register()`.
- Read-only access except album creation.
- Every tool and route works against an injected `PhotosDB` so tests can use osxphotos' own MIT test libraries (from the osxphotos repo `tests/` folder) stored under `lumina/tests/fixtures/` or downloaded by a test helper into `/Volumes/SamirDrive/...` (never `/tmp`, never `$HOME`).
- Dependencies: `osxphotos` and `photoscript` with upper bounds in `python_dependencies` (catalog rule). No other new deps.
- One runnable test file per module; `hermes plugins validate <path> --install-deps` must pass.

## Lane B (frontend, desktop/plugin.js)

Plain ESM. Only imports: `@hermes/plugin-sdk`, `react`, `react/jsx-runtime`. No JSX. Theme vars only, no hardcoded colors. `defaultEnabled: false` is NOT needed (plugin id `lumina`).

1. Transcript directive `lumina-photos`: the agent writes a paragraph `::lumina-photos{ids="u1,u2,u3" title="Mom at the beach"}`.
   - Renders a responsive grid of thumbnails (fetch with `ctx.rest('/thumbs?uuids=...&size=s')`, React Query, key `['lumina','thumbs',ids]`).
   - Up to 12 tiles inline, then a "+N more" tile that opens the side pane with the full set.
   - Click a tile → `ctx.rest('/open?uuid=...')`. Missing `data_url` → neutral placeholder tile with the date.
   - Validate attrs (uuids are `[0-9A-F-]{36}` style; ignore anything else); render nothing on garbage.
2. Side pane (`PANES_AREA`, id `pane`, title `Lumina`, placement `right`, width `340px`): shows the last grid that was opened ("+N more" or a palette command), medium thumbnails, plus the `/status` result (photo count, access state with the Full Disk Access instructions when `no_access`).
3. Palette command "Lumina: Open photo pane".

Until Lane A's backend exists, develop against a mock that returns the documented `/thumbs` and `/status` shapes.

## Skill (skills/lumina/SKILL.md)

Short: when the user asks about their photos, call `lumina_search` (map "mom", "Goa", "beach", "passport" to person/place/label/text), then answer in one sentence and put the `::lumina-photos{ids="..." title="..."}` directive on its own paragraph. For albums, reuse the uuids from the previous search. If a tool returns `no_access`, explain the Full Disk Access step plainly.

## Demo acceptance (what the video shows)

1. "Find my passport" → grid with the passport photo, under 5 s after the library is warm.
2. "Show me photos of Mom at the beach" → grid; "+N more" opens the pane.
3. "Make an album called Goa Trip with photos from Goa last December" → album appears in Apple Photos.
4. Permission missing → friendly instructions instead of a stack trace.
