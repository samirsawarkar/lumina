# Lumina — ask your photo library anything

Search people, places, scenes, and text inside your Apple Photos library right from Hermes, build albums in a click, or relive memories on this day — completely private and running on your Mac.

![Lumina in Hermes Desktop](docs/img/hero.png)

![Lumina Demo](docs/img/demo.gif)

---

## What you can ask

Lumina understands how you remember your photos. You can ask naturally:

- **"Find my passport"** — searches text detected inside documents, ID cards, and receipts using Apple's on-device OCR.
- **"When does my passport expire?"** / **"What's the Wi-Fi password in my photos?"**: Lumina reads the text inside the photo and answers with just the fact, showing the photo it came from.
- **"Make a memory of our Goa trip"**: Lumina picks the best 12 photos and makes a 20-second movie with slow zooms and captions. It plays right in Hermes, or arrives on your phone.
- **"Is anything of mine expiring soon?"**: Lumina reads your photographed passport, cards and memberships and tells you what expires first. Ask Hermes to remind you before the date.
- **"Photos of Mom at the beach"** — combines recognized family members with scene classifications.
- **"Make an album of our Goa trip"** — finds photos from your holiday and creates a ready-to-share album in Apple Photos.
- **"Send me my passport photo" (from your phone)** — message Hermes while on the go via Telegram or WhatsApp and get the photo sent directly to your chat.
- **"On this day"** — surface favorite memories captured on today's calendar date across previous years.
- **"Show me receipts from last December"** — finds expense records by date range and extracted text.
- **"Show screenshots from this week"** — gathers recent screenshots without mixing in camera shots.

---

## Private by design

Your photos are deeply personal. Lumina is built with a strict privacy architecture:

- **Runs 100% on your Mac:** All database queries and thumbnail rendering happen locally on your computer.
- **No cloud, no API keys:** Lumina itself makes no network requests and needs no accounts. Photos leave your Mac only when you ask Hermes to send one to your phone: that photo is then delivered through the messaging app you chose (Telegram, WhatsApp, …).
- **Uses Apple Photos' on-device analysis:** Powered by `osxphotos`, Lumina reads the faces, scene labels, and OCR text that macOS has already indexed locally on your device.
- **Never deletes photos:** Lumina is strictly read-only, with the single exception of creating new albums in Apple Photos when you explicitly ask. There is no delete or cleanup capability.
- **Local thumbnails only:** Image previews are generated on demand and served only to your local Hermes Desktop window over a local loopback connection (`localhost`).

### What the AI sees (and what it doesn't)

We believe in complete transparency:

- **What your chosen AI model sees:** When Hermes searches your library, it receives the photo metadata needed to answer your request — dates, place names, recognized people names, scene labels, and short snippets of detected text (up to 80 characters). When you ask a question about a photo ("when does my passport expire?"), the full text of that photo (at most 5 photos) is sent so the model can answer.
- **What your chosen AI model never sees:** the images themselves and deleted or hidden photos. When you ask to send a photo to your phone, the model only handles the attachment line for the exported copy, not the image.

---

## Install

### One-Click Install

Click the link below to install Lumina directly into Hermes Desktop:

<a href="hermes://plugin/install?repo=samirsawarkar/lumina&enable=1">Install in Hermes</a>

### CLI Alternative

If you use the Hermes command line, install the plugin with:

```bash
hermes plugins install samirsawarkar/lumina
```

Then open Hermes Desktop and turn on **Lumina** under **Settings > Plugins > Desktop plugins**.

---

## First run & permissions

On your first search, macOS prompts for permission to access your photo data and automate Apple Photos.

### 1. Full Disk Access (Reading Photos)

Lumina reads Apple Photos' local database. macOS requires Full Disk Access for this read-only access:

1. Open **System Settings > Privacy & Security > Full Disk Access**.
2. Toggle on **Hermes** (or your terminal application if running Hermes from the command line).
3. Restart Hermes.

### 2. Automation (Creating Albums & Viewing Photos)

When you ask Lumina to create an album or click a photo to open it in Apple Photos, macOS requests Automation permission:

1. A system dialog asks: *"Hermes wants to control Photos"*. Click **OK**.
2. If you ever need to verify or re-enable this, open **System Settings > Privacy & Security > Automation**, find **Hermes**, and turn on **Photos**.

---

## How it works

Lumina connects Apple Photos with Hermes through a full-stack plugin architecture:

### Plugin Tools

- `lumina_search`: Fast metadata and OCR search across people, places, scene labels, dates, media types, and recognized text. Returns photo IDs and metadata, never disk paths.
- `lumina_create_album`: Automates Apple Photos to create or update albums using photo IDs from previous searches.
- `lumina_library_info`: Checks library status, counts of photos and videos, and top recognized people.
- `lumina_export`: Sends full photos to messaging channels (like Telegram and WhatsApp) using Hermes `MEDIA:` attachments.
- `lumina_read_text`: Reads the full on-device OCR text of up to 5 photos so Hermes can answer questions like "when does my passport expire?".
- `lumina_make_memory`: Renders up to 12 photos into a ~20 s MP4 (Ken Burns zoom, captions) with ffmpeg, or an animated GIF if ffmpeg is not installed. Returned as a `MEDIA:` line, so Hermes Desktop plays it inline and messaging apps deliver it.
- `lumina_expiry`: Finds expiry dates in the on-device OCR text of document photos and lists them soonest first.
- `lumina_on_this_day`: Gathers photos taken on today's month and day from earlier years.

### Lifecycle Hooks

- `pre_llm_call`: Detects photo-related requests in chat and provides prompt context nudging the agent toward `lumina_search`.
- `post_tool_call`: Remembers the visual photo grid directive generated during the search.
- `transform_llm_output`: Automatically appends the photo gallery directive (`::lumina-photos{...}`) if the language model forgets to include it.
- `pre_tool_call`: Validates album creation requests to protect against empty names or invalid IDs before touching Photos.

### Desktop UI & Thumbnail API

- **Inline Transcript Directive:** `::lumina-photos{ids="..." title="..."}` renders a native, responsive photo grid right inside the conversation thread. Clicking any thumbnail highlights that photo in Apple Photos.
- **Side Pane:** A dedicated right-hand layout pane (`PANES_AREA`) displays medium-resolution previews for large result sets alongside library health and status.
- **Local Thumbnail Service:** An internal FastAPI service (`/api/plugins/lumina/thumbs`) serves cached, downscaled JPEGs extracted directly from Apple Photos' derivative caches.

---

## Requirements

- **Operating System:** macOS (tested on macOS 26 on Apple Silicon).
- **Hermes Agent:** Hermes >= 0.23.
- **Optional:** `ffmpeg` (`brew install ffmpeg`) for MP4 memory videos; without it Lumina makes an animated GIF.
- **Apple Photos:** the library Photos last opened. To use a different one, set `LUMINA_LIBRARY` to its `.photoslibrary` path.

---

## Credits & License

- **[osxphotos](https://github.com/RhetTbull/osxphotos)** by Rhet Turnbull (MIT License) — powers on-device database queries, metadata extraction, and OCR access.
- **[PhotoScript](https://github.com/RhetTbull/photoscript)** by Rhet Turnbull (MIT License) — provides AppleScript automation for album creation and photo spotlighting.
- Demo placeholder images courtesy of Unsplash via Lorem Picsum.

Lumina is released under the [MIT License](LICENSE). Copyright © 2026 Samir Sawarkar.
