garm-proxmox-provider/docs/logging.md

# Logging

This document explains how the provider configures logging at runtime, which environment
variables control behavior, examples for running the CLI with different logging modes,
and tips for production deployments (systemd / journald / files).

The CLI sets up logging centrally in `cli._setup_logging()` so that:

- A console/stderr handler is installed by default (unless a logging configuration already
  provided handlers).
- An optional rotating file handler can be enabled with `GARM_LOG_FILE`.
- JSON formatting is optional and enabled using `GARM_LOG_JSON` (requires `pythonjsonlogger`).
- Log level is controlled via `GARM_LOG_LEVEL` (preferred) or the legacy `GARM_DEBUG` flag.

These variables are respected early when the CLI (entrypoint) runs, so set them in the
environment of the process (systemd unit, container env, CI step, etc).

---

## Environment variables

- `GARM_LOG_LEVEL` — preferred way to set the log level. Use `DEBUG`, `INFO`, `WARNING`,
  `ERROR`, or `CRITICAL` (case-insensitive). If the value is invalid, the code falls back
  to `INFO`.
- `GARM_DEBUG` — legacy boolean flag. If set (to any non-empty value) and
  `GARM_LOG_LEVEL` is not set, the log level will be `DEBUG`. If neither is set, the
  default level is `WARNING`.
- `GARM_LOG_FILE` — optional path to a file where logs should also be written. When set,
  the CLI will add a rotating file handler that rotates at 10 MB with 5 backups by default.
  The code attempts to create the directory for the file (if any) but ignores failures
  creating it.
- `GARM_LOG_JSON` — enable JSON-formatted logs (progressively). Accepts `1`, `true`, or
  `yes` (case-insensitive). When set, the CLI will try to use `pythonjsonlogger.JsonFormatter`;
  if that import fails it falls back to plain-text formatting.

---

## Behavior summary

- The root logger is configured only if it has no handlers already. This allows an external
  logging configuration (for example, a process supervisor or a test harness) to take
  precedence.
- The default console handler writes to `stderr`.
- File logging (rotating) uses:
  - maxBytes: 10 *1024* 1024 (10 MB)
  - backupCount: 5
- JSON formatting requires `pythonjsonlogger` on the PYTHONPATH. If it's missing, the
  provider falls back to a human-readable text formatter even when `GARM_LOG_JSON` is set.

---

## Examples

Set the log level to DEBUG and print text-formatted logs to stderr:

```/dev/null/examples.md#L1-4
export GARM_LOG_LEVEL=DEBUG
uv run garm-proxmox-provider --help
```

Enable file logging (rotating) and JSON output:

```/dev/null/examples.md#L1-4
export GARM_LOG_JSON=1
export GARM_LOG_FILE=/var/log/garm-proxmox-provider/garm.log
uv run garm-proxmox-provider list-templates
```

If `GARM_LOG_JSON=1` and `pythonjsonlogger` is installed, a sample JSON log entry may look like:

```/dev/null/examples.md#L1-6
{"asctime":"2024-01-01 12:00:00,000","levelname":"INFO","name":"garm_proxmox_provider.cli","message":"Starting CLI"}
```

If the text formatter is used, the same message appears in a readable form:

```/dev/null/examples.md#L1-4
2024-01-01 12:00:00,000 INFO garm_proxmox_provider.cli: Starting CLI
```

---

## systemd unit example

Below is a minimal systemd service snippet that sets environment variables for the service.
Adjust paths and user/group as appropriate for your deployment.

```/dev/null/examples.md#L1-14
[Unit]
Description=GARM Proxmox Provider
After=network.target

[Service]
Type=simple
User=garm
Group=garm
Environment=GARM_LOG_LEVEL=INFO
Environment=GARM_LOG_JSON=1
Environment=GARM_LOG_FILE=/var/log/garm-proxmox-provider/garm.log
ExecStart=/opt/uv/bin/garm-proxmox-provider
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Note: If you run the service under systemd and prefer journal integration, you can omit
`GARM_LOG_FILE` and rely on `journalctl -u your-service` instead. Systemd captures
stderr/stdout and stores structured fields; JSON logs sent to stderr will be visible in the
journal, too.

---

## Parsing JSON logs

If you enable JSON logs and want to pretty-print them on the console for debugging:

```/dev/null/examples.md#L1-3
uv run garm-proxmox-provider list-templates 2>&1 | jq .
```

(Assumes `jq` is installed and `GARM_LOG_JSON=1` is set).

---

## Troubleshooting

- No JSON output even though `GARM_LOG_JSON=1`:
  - Ensure `pythonjsonlogger` is installed in the runtime environment used to invoke the CLI.
  - If `pythonjsonlogger` is missing, the provider will silently fall back to plain-text logs.

- Log file not created or empty:
  - The code attempts to create the log file directory if it exists in the path, but any
    exception during directory creation is ignored. Ensure the process has permissions to
    create the path and write files there.
  - If the root logger already had handlers configured by another part of your environment,
    the CLI will not add its own handlers. This can happen during tests or when embedding
    the provider into other programs.

- Too verbose / missing logs:
  - Set `GARM_LOG_LEVEL=DEBUG` to increase verbosity (or `INFO` to show more than `WARNING`).
  - For library-specific debugging, you can configure additional loggers in your environment
    (e.g., using a separate logging config file or a wrapper that configures `logging.getLogger("proxmoxer")`).

---

## Recommendations

- For production deployments:
  - Prefer `GARM_LOG_FILE` for durable logs and make sure log rotation and retention match your
    operational policies. The built-in rotation parameters are a reasonable default but may
    be adjusted by adding an external log-rotation policy or by wrapping the service in a
    supervisor that manages log rotation.
  - Consider structured (JSON) logs if you ship logs to ELK/Graylog/Cloud logging platforms.
    Ensure `pythonjsonlogger` is included in your runtime dependencies if you want JSON output.
- For local development:
  - `GARM_DEBUG=1` is a convenient way to get more verbose logs without setting the full
    level string: `export GARM_DEBUG=1`.
  - Use `uv` to create a reproducible development environment (`uv sync --dev`) before
    running the CLI so that optional dependencies (like `pythonjsonlogger`) are available.

---

## Where the code lives

The logging bootstrap is implemented in the CLI module; look at:

- `src/garm_proxmox_provider/cli.py` — `_setup_logging()` performs the root-logger setup
  and honors the environment variables documented above.

This single, small entrypoint ensures consistent logging behavior regardless of which
subcommand is invoked.

---

If you'd like, I can:

- Add a short `docs/dev.md` section with `uv` + env examples specific to your environment,
- Add a small systemd unit file under `contrib/` for convenience,
- Or update the README to highlight the `GARM_LOG_*` variables and JSON formatting requirements.
