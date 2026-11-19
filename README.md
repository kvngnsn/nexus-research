# Nexus Research

Open-source molecular synthesis research for macOS. Bring your own AI connection; findings save directly to a folder you choose.

**No Nexus account, hosted database, telemetry, or ownership claim over your findings.** The software is [MIT licensed](LICENSE). Third-party dependencies retain their own licenses.

## Get started

1. Download **Install Nexus.dmg** from [Releases](https://github.com/kvngnsn/nexus-research/releases/latest), open it, and drag **Nexus Research** into Applications.
2. Choose a findings folder on your computer or mounted drive.
3. Connect your own API key, signed-in CLI, or local model server.
4. Add a target structure, set a budget, and start research.

The Mac app requires **Apple Silicon and macOS 14+** and includes its runtime. It is ad-hoc signed, not Developer ID signed or notarized; macOS may block first launch. Building from source is also supported.

## Your models, your files

| Connection | What you provide |
|---|---|
| OpenAI / Anthropic API | Your API key and exact model ID; keys stay in macOS Keychain |
| Codex / Claude Code CLI | An installed CLI signed in to your own account |
| Local LLM | An OpenAI-compatible server on localhost, its `/v1` URL and model ID |

Each run writes **JSON and Markdown** automatically after every response and when the run finishes. Default: `~/Documents/Nexus Research/`. Local recovery history preserves received work if the selected drive becomes unavailable; no further model calls start after a save failure. Existing runs retain their original folder when you choose a new one.

Remote providers receive prompts only when you start or resume a run. Prompts include your targets and a bounded excerpt of relevant local pathway history. Provider terms and charges apply. Local LLMs use your existing server; Nexus does not download weights. Nothing is sent to a Nexus service.

Results are research hypotheses. Graph checks establish structural consistency, not experimental feasibility. Sources and costs require verification; missing costs remain unknown.

## Develop

```sh
cd code
make dev
make run       # http://127.0.0.1:8774
make check
```

See the [developer guide](code/README.md), [usage guide](code/docs/usage.md), and [roadmap](todolist.md). Source, tests and build tools live in `code/`. Installers are release assets, not source-history files. Private records, credentials, dependencies and builds are ignored.
