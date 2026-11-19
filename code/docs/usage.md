# Using Nexus Research

1. Choose **Save findings → Choose folder** in the Mac app, or enter an existing absolute directory and select **Use folder** in the browser.
2. Under **Manage connections**, add your own OpenAI/Anthropic key, connected Codex/Claude CLI, or localhost OpenAI-compatible model. Use the exact model ID. Nexus does not download models or make a paid connection test.
3. Paste up to twelve SMILES targets, choose the connection and set the budget.
4. Start the operation. Pause, stop or resume explicitly.

Findings automatically update as `nexus-<run-id>.json` and `nexus-<run-id>.md` in your chosen folder. **Show folder** opens it in Finder. JSON includes structured findings, visible model responses and reported usage. Markdown is a readable report. Export buttons let you save an additional copy, including older history. Choosing another folder affects new runs only.

No Nexus database connection or contract acknowledgment is required. Nexus makes no ownership claim over your findings. API keys remain in macOS Keychain; CLI login stays with its own app. When using a remote provider, targets and relevant local pathway excerpts are included in its prompts. A fully local model keeps these requests on localhost.

## Runs and recovery

- **One analysis per target:** one call for each target, subject to the total budget.
- **Timed exploration:** continues until a time, token or call limit stops it.
- **Finite methods / no progress:** stops after the configured period without a new graph-checked route. This is not proof that all methods were found.

If the destination is missing or unwritable, no run starts. If saving fails during a run, subsequent calls stop and received work stays in local recovery history. Reconnect the drive and explicitly resume within the original budget, or export the saved run elsewhere. A restart never automatically resumes work. Unknown in-flight usage prevents budget-safe resume.

CLI usage is known after each call, so one in-flight call can exceed a token budget. Remote requests may continue billing after cancellation. Dollar figures are estimates from rates you enter. Sources, chemistry and commercial viability are not verified by the app.

Earlier private previews use a separate Keychain namespace. Re-add API keys in this edition; existing local research history is preserved. CLI login is unchanged.
