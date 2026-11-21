# Filex writer release packaging
Preserve Git archive modes for reviewed public source payloads: tracked regular
files use 0644 (0755 if Git marks executable), and archive directories use
0755. The writer's Dockerfile COPY preserves those read bits even though the
payload in the image is root-owned. Neither Docker's configured USER nor the
registered service's UID/GID may be changed to compensate for a bad export.

Do not include secrets, runtime state, locally ignored files, or private launch
configurations in this export. A cold smoke test with the registered UID/GID,
read-only root filesystem, no capabilities, no-new-privileges, loopback mapping,
and an entirely temporary fixture root/token must return the exact authenticated
/v1/capabilities response before release review. Never use production credentials,
data, or mounts in the fixture.
