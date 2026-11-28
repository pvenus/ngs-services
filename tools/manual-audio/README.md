# Owner-local manual WAV registration

This registers an existing file as a published Binari BGM without invoking any generator or changing the original. PDS authenticates the owner and delegates to its scoped Filex writer; the writer validates PCM16 WAV and SHA and publishes an atomic folder. The file is never transcoded. Formats: canonical PCM16 WAV, mono or stereo, 8–96 kHz, 10–120 seconds, up to 24 MiB. Optional source ID and SHA pin the pre-existing generated original separately. Exact replay is safe.

Rollout: deploy the updated NGS reader before enabling registration on local Filex/PDS, so that the first canonical file arrival indexes correctly. Feature is off by default. Opt in by configuring the loopback PDS service with `PDS_MANUAL_AUDIO_IMPORT_TOKEN_FILE` pointing to an absolute canonical path to a private owner-only file (mode 0600, no symlink) holding a random base64url token (43–128 characters). Use a separate secret from Filex and all browser credentials. Do not include it in packages or shell arguments. This endpoint is not a Space/Viewer upload API.

```sh
python3 tools/manual-audio/import-wav.py /path/to/edited.wav \
    --title '비나리 · 타이틀 BGM (27초 루프)' \
    --token-file /private/absolute/path/to/token \
    --source-id request.official.<64-hex-id> --source-sha256 <64-hex-hash>
```

Then check the normal NGS audio catalog; fetch the returned audio URL and verify SHA-256 against the receipt. The manual item is distinct from the generated track. Generated-only separation/deletion actions are not enabled for imports.
