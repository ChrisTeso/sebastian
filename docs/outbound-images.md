# Image replies

The host captures completed `imageGeneration` items from the active native thread and turn. A final answer may contain text, generated images, or both. Input images, image-view tool results, other turns, and failed turns are not exported. PNG/JPEG/GIF/WebP output is limited to four images of 8 MB each. No transcript scraping or model-provided file paths are used for delivery.

Slack uses the existing bot's `files_upload_v2` scope and the policy-approved channel/thread. Messages sends a private temporary file to the policy-approved chat ID, using its existing iMessage/MMS/RCS service. Plain SMS does not carry images; carrier delivery requires the Mac's working phone relay and carrier support. A local Messages database receipt establishes that Messages accepted the attachment, not that a carrier delivered it.

Every text/image part is planned before sending. The ledger retains HMAC fingerprints and provider receipts, not image bytes. Messages filenames contain a random per-image identifier used to match outgoing attachments and suppress self-chat loops. Uncertain sends are not automatically replayed. Temporary image files are mode 0600 inside mode 0700 directories and removed after receipt observation or the bounded timeout.

Verification: `.venv/bin/python -m unittest discover -s tests`. Current live evidence and independent review are recorded in `docs/progress.md` and `docs/evidence/`.
