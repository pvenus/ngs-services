# Background entry removal
A published background in the flat legacy collection consists of its content-addressed PNG and JSON metadata.
NGS submits **two independent common `file.delete` requests** for a selected item: one exact `<sha256>.png` and
one exact `<sha256>.json` under `canonical-files/binari/cinematics/backgrounds`. Each request pins
`expected.sourceRequestId` from that item's published metadata. There is one target per request; never pass
the collection directory. Both requests may be processed in either order; track partial admission/completion separately.
The shared file worker uses the common authorized PDS/Filex path. A Filex-owned private tombstone ties the two
targets to the published bytes and prevents late background publisher replays from recreating the retired digest.
Private generation receipts and the common recoverable quarantine are not published or synchronized as catalog items.
A different digest can still be published; exact retired bytes cannot be reused as a live background.
Enable only where PDS `PDS_WORKFLOW_BACKGROUND_DELETE=1`, shared Filex cinematics scope and generic
file-worker transport are configured. The dedicated PDS check is GET `/v1/storage/delete-target/background`.
The older shared readiness reply intentionally does not advertise this newly handled root.
