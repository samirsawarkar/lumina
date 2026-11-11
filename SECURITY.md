# Security

Lumina handles photos of identity documents, so security reports are taken seriously.

## Reporting a problem

Please do not open a public issue for a security problem. Use GitHub's private reporting instead: **Security > Report a vulnerability** on this repository.

Useful details: macOS and Hermes versions, the request you made, and what happened. Please do not attach real documents; the made-up ones in `tests/fixtures` are enough to reproduce most problems.

## What Lumina promises

- It makes no network requests and stores no credentials.
- It never changes or deletes photos; it can create albums when asked.
- Safe Send delivers a copy only after re-reading it and finding no sensitive text. If that check fails, nothing is delivered.
- Leak Guard blocks outbound tool calls that carry a raw image of an ID document. It covers the common routes (file paths in terminal, code, browser and message tool calls). It is a safety net, not a guarantee against a determined attacker, and it fails open if its own check errors.

## Out of scope

- Documents the on-device text recognition cannot read (blurred, tiny or handwritten in an unsupported script).
- A user explicitly asking for the original to be sent.
