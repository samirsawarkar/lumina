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

Lumina is read-only apart from creating albums. It cannot delete photos.
