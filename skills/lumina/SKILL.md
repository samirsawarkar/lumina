---
name: lumina
description: Find photos in the user's Apple Photos library (people, places, scenes, text in images) and make albums.
---

# Lumina

Use when the user asks about their photos ("find my passport", "photos of Mom at the beach", "make an album of our Goa trip").

1. Call `lumina_search`. Map the request to filters: names to `person`, locations to `place`, scenes ("beach", "dog") to `label`, things written in an image ("passport", "receipt") to `text`, time words to `date_from`/`date_to` (ISO dates; resolve "last December" from today's date).
2. Answer in one short sentence and paste the result's `display` value (`::lumina-photos{...}`) on its own line; Lumina also appends it if you forget. Do not list uuids or file paths, and do not judge whether images are real or samples. Never use terminal or file search for photos. If `count` is 0 there is no `display`: say so and suggest a broader search.
3. For albums, reuse the uuids from the previous search (search again if there are none) and call `lumina_create_album`. Confirm the name and count.
4. Call `lumina_library_info` when the user asks what Lumina can see, or to check permissions.
5. If a tool returns `no_access`, explain plainly: System Settings > Privacy & Security > Full Disk Access, enable Hermes (or the terminal running it), restart. If `photos_app_error` on albums, Photos may need Automation permission under Privacy & Security > Automation.

6. "On this day" / memories: call `lumina_on_this_day` (optional ISO `date`, default today) and paste its `display` like a search result; mention the `by_year` counts.
7. On a messaging platform (Telegram, WhatsApp, ...) the grid cannot render. To send photos ("send me my passport photo"), call `lumina_search`, then `lumina_export` with up to 10 uuids, and put each `media` line (`MEDIA:/...jpg`) on its own line in the reply, with no other text on that line. Do not paste the `::lumina-photos` directive there. Photos only in iCloud are reported in `skipped`; tell the user.
8. Scheduling: users can ask Hermes itself ("every morning at 9, send me my On this day photos") and Hermes creates the scheduled job; that job should call `lumina_on_this_day` then `lumina_export`. The plugin has no scheduler of its own.
9. Questions answered by a photo ("when does my passport expire?", "what's my Wi-Fi password?"): `lumina_search` with `text` for the document, then `lumina_read_text` with the best 1-3 uuids, and answer with just the fact in one short sentence. Its `display` is appended automatically; do not list uuids.
10. "Is anything expiring?" / "when do my documents expire?": call `lumina_expiry` (optional `within_days`), lead with anything `expired` or `soon` (with days left), then list the rest briefly; its `display` is appended automatically. To get reminders, ask Hermes to schedule one (e.g. a month before the date).
11. "Make a memory / video / slideshow of my Goa trip": call `lumina_make_memory` with the search filters (or uuids from the last search), then put the returned `media` line (`MEDIA:/...mp4`) on its own line with no other text on it, plus one warm sentence. It plays inline on desktop and is sent as a video on Telegram/WhatsApp.
12. Sending or sharing a photo of an ID or document, or "hide my number": `lumina_search` for it, then `lumina_redact` with `uuid` and `preset` (hotel/landlord -> hotel, airline/check-in -> airline, anyone else or unsure -> all). Never `lumina_export` the original: the Guardian blocks it unless the user explicitly asks for the original (then `allow_original=true`). Tell the user in one sentence what kinds were hidden (never the values) and that Lumina re-read the copy and 0 sensitive items remain. Desktop shows the before/after (`display`), messaging gets the `media` line; Lumina appends the right one if you forget.

Lumina is read-only apart from creating albums. It cannot delete photos.
