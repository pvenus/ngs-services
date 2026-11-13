# Local stack launcher

See [the current startup and scope instructions](../README.md). Use an explicit absolute `--storage-root`; there is no legacy external-volume default. Services bind only 127.0.0.1. `--port-base` selects the complete seven-service group; `--print-plan` is read-only. Signals stop only child processes created by this launcher. Existing databases and signing secret are preserved.
