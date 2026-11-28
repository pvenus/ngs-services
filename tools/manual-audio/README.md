# Owner-local manual WAV registration

Registers an existing WAV as a published Binari BGM without invoking a generator or changing the original. PDS authenticates the owner; scoped Filex validates PCM16 and SHA-256, then atomically publishes the folder. The file is never transcoded. Accepted: canonical PCM16 WAV, mono/stereo, 8–96 kHz, 10–120 seconds, up to 24 MiB. Optionally pin the original generated audio by ID and SHA. Exact replay is safe.

## OPS rollout and owner provisioning

1. Through OPS, deploy the updated Local NGS reader before enabling registration, so that the first canonical file arrival is indexed correctly.
2. The owner may opt in on the Mac by creating the fixed marker below **before** the coupled `Filex 저장기` deployment. Without this marker or a development override, the endpoint is off. This command refuses to replace a token, never prints it, and does not change LaunchAgents, unified binding, OPS receipts, or selectors.

```sh
python3 - <<'PY'
import os, secrets, stat
from pathlib import Path
root = Path.home() / 'Workspaces/PDS/data'
st = root.lstat()
if root.resolve(strict=True) != root or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
    raise SystemExit('Expected a canonical owner-only PDS storage directory (0700).')
secret = root / '.manual-audio-import-token'
fd = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, 'wb') as stream:
    os.fchmod(stream.fileno(), 0o600)
    stream.write((secrets.token_urlsafe(48) + '\n').encode('ascii'))
    stream.flush()
    os.fsync(stream.fileno())
dir_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    os.fsync(dir_fd)
finally:
    os.close(dir_fd)
PY
```

3. Deploy Filex writer and Backend Stack/PDS using the coupled OPS target from the same Services commit. Do not inject this setting into the signed LaunchAgent selector: it invalidates predecessor proof for the next OPS deployment. Local PDS loads the marker at startup under its existing `--storage-root`. Binding must be loopback. Bad mode, symlink, hard link, non-regular file or malformed secret fails startup closed. Use `PDS_MANUAL_AUDIO_IMPORT_TOKEN_FILE` only for an explicit dev/test override. Do not reuse a writer or browser credential, expose the token in a terminal command argument, include it in release packages or copy it into OPs evidence. To opt out, remove the marker and restart through the approved operating workflow.

## Register and verify

```sh
python3 tools/manual-audio/import-wav.py /path/to/edited.wav \
    --title '비나리 · 타이틀 BGM (27초 루프)' \
    --token-file "$HOME/Workspaces/PDS/data/.manual-audio-import-token" \
    --source-id request.official.<64-hex-id> --source-sha256 <64-hex-hash>
```

Then check the normal NGS audio catalog after its sync, fetch the audio URL, and verify its hash against the receipt. The imported BGM is distinct from the original. Generated-only separation/deletion are not available for it. This endpoint is not a Space/Viewer upload API; earlier clients can sync the generic files but omit the entry from their audio catalogs.
