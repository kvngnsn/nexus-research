# Roadmap

Update this file after each task with completed work, validation and remaining work.

## Current release · 0.4.0

- [x] Remove hosted database connections, reporting, enrollment tools and configuration.
- [x] Remove ownership drafts, acknowledgment gate and company reporting notices.
- [x] Retain user API keys, connected CLIs and localhost LLM support.
- [x] Automatically save JSON and Markdown to a user-selected directory; preserve local recovery history.
- [x] Add MIT licensing and public documentation without private collaborator information.
- [x] Complete isolated tests, packaged verification and privacy review.
- [ ] Publish clean public source history and a rebuilt Mac installer.

## Future work

- [ ] Synthesis cost estimates with dated sources, ranges and explicit missing inputs.
- [ ] Retrosynthesis provenance distinguishing literature routes, generated proposals and hybrids.
- [ ] Optional patent screening with source links and uncertainty; no definitive clearance claims.
- [ ] Developer ID signing, notarization and clean-device validation.

**Validation:** 50 synthetic tests pass, plus lint, formatting and JavaScript checks. The packaged app and read-only installer pass isolated local-model checks. Native UI QA verifies folder selection, CLI discovery and completed findings. Secret scanning found no leaks; the source and 1,191 bundled Python modules passed private-marker checks. No paid model calls were made.

**Release status:** Public source and version 0.4.0 installer prepared for publication. Developer ID signing and notarization remain open.
