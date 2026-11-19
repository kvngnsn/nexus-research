# Developing Nexus Research

Read [contributor instructions](AGENTS.md). Run commands from `code/`. Python serves the workspace on loopback; Swift/AppKit hosts it in WKWebView. There is no hosted Nexus backend.

## Setup and checks

Python 3.11, Node 20+ for formatting, and Apple's command-line developer tools for the Mac app:

```sh
make dev
make run
make check
make app
make verify-app
make installer
```

`make check` runs lint, formatting, JS syntax and synthetic tests. No real model calls, accounts, private research stores or Keychain credentials are used by the tests. `make verify-app` starts the frozen engine against an isolated local fixture and checks automatic file saves, removed reporting routes, assets and signing. `make installer` produces `../Install Nexus.dmg`; publish it as a GitHub release asset.

## Code map

| Path | Purpose |
|---|---|
| `nexus_research/research.py` | Budgets, provider selection, cancellation and run lifecycle |
| `nexus_research/research_store.py` | Local SQLite recovery history, events and pathway deduplication |
| `nexus_research/findings.py` | Atomic JSON/Markdown saves in the selected folder |
| `nexus_research/providers.py`, `transport.py` | User API/CLI/local-model adapters and cancellation |
| `nexus_research/server.py` | Loopback HTTP API and static allowlist |
| `web/` | Monochrome interface and locally bundled Source Code Pro |
| `desktop/` | Native app, folder picker and Save dialogs |
| `scripts/` | Packaging and verification |
| `tests/` | Synthetic fixtures and regression tests |

## Storage and privacy

The native app keeps recovery history in `~/Library/Application Support/Nexus Research/research/`. Findings default to `~/Documents/Nexus Research/`; the folder picker supports any existing writable folder, including a mounted drive. Development checkpoints default to ignored `code/data/research/`.

| Variable | Purpose |
|---|---|
| `NEXUS_PORT` | Loopback port; default 8774, 0 chooses an available port |
| `NEXUS_DATA_DIR` | Development data root |
| `NEXUS_RESEARCH_DIR` | Override local recovery/checkpoint folder |
| `NEXUS_OUTPUT_DIR` | Initial findings folder, before a user preference is saved |
| `NEXUS_READY_FILE` | Native startup handshake |
| `NEXUS_NATIVE_DATA_DIR` | Isolated native QA data root |

Plain local files and SQLite are not encrypted. API credentials use macOS Keychain; CLI authentication stays with its own CLI. Exported JSON omits local paths, account aliases and credential references. Prompts and model text remain research content; inspect them before sharing files. A user-selected cloud-synced folder may sync through that user's storage provider.

Existing local history remains local. Schema 4 preserves old records and inert legacy tables without reporting or deleting them. No account enrollment, heartbeat, upload queue processing, reporting configuration, ownership contract or opening acknowledgment remains in the app. The software license covers the code; Nexus makes no ownership claim over findings.

## HTTP boundary

The server binds only to `127.0.0.1`. Host and Origin checks and `X-Nexus-Token` protect operations. Static assets are explicitly allowlisted. No permissive CORS or public file-serving route exists.

- `GET /api/workspace`: local session token, no contract gate.
- `GET /api/research/status`, `/run?id=…`, `/export?id=…`: local state and exports.
- `POST /api/research/output`: validate/save the findings directory for new runs.
- `POST /api/research/connection`, `/connection/delete`: user provider aliases.
- `POST /api/research/start`, `/control`: bounded operation and explicit pause/stop/resume.
- `GET /api/health`, `/api/structure?smiles=…`: app identity and local structure preview.

Each model completion atomically checkpoints its response, new pathway claims and events in SQLite, then writes findings before another model call. Individual output files use flushed temporary files and atomic replacement; JSON is authoritative if an interrupted write leaves the Markdown behind. A failed destination stops work and retains recovery history. Resume is explicit and respects the original deadline and usage uncertainty.

The public repository starts with clean history. Do not add private records, old archives, deployment configuration or locally generated installers to source control. Preserve third-party license notices. Signing and notarization remain release work.
