# Verification

Local checks were run on macOS with Python 3.14.7 and Codex CLI 0.155.0
on 2026-09-18. The automated suite uses only the Python standard library and
does not need Codex, a TypeSafe key, or network access.

## Automated checks

`python3 -m unittest -v`: 18 tests passed.

Coverage includes:

- Invalid API decisions, bounded HTTP retries, private key loading, and RPC errors.
- Conversation context limits, streamed output, explicit approval, and cancellation.
- Usage-window pacing, missing data, mode selection, repair limits, and stage order.
- Install and update in a temporary home directory, including paths with spaces.
- Native Codex command forwarding and prevention of wrapper recursion.
- Settings validation, atomic writes, private permissions, live reload, and CLI precedence.
- Protection of existing commands, rollback after a failed update, and preservation of shell symlinks.
- Uninstall and credential removal without changing the original executable.

GitHub Actions runs the suite on Ubuntu and macOS with Python 3.10 and 3.14.

## Live checks

Real TypeSafe and Codex calls verified:

- Route selection from the account's available models and reasoning levels.
- A completed Codex turn from the installed command outside its source checkout.
- Conversation history across a model change and across several interactive turns.
- File creation and command execution in a temporary project.
- Ctrl-C interruption followed by another successful turn in the same session.
- Account allowance display and usage-based route selection.
- A failed check, a new Jev decision, a successful repair, and the next task stage.
- Installer checks for Codex sign-in, model access, and a valid TypeSafe response.
- An agent changed a session's saved reserve to 15% through the settings CLI and
  read it back. The test used a separate settings file and preserved user defaults.

The repair test restricted the candidate model to Luna. Jev selected low effort
for the first stage, medium effort for the repair, and low effort for the next
stage. This verifies the control flow, not optimal model choice.

## Limits

These checks do not establish cost savings, routing accuracy, or task quality
across a representative workload. Usage preferences are initial policy rules.
The account API does not supply per-model consumption prices.

MCP forms and additional permission requests have not been checked against live
external servers. The client supports text input and turn-boundary model changes;
it does not provide the full native Codex terminal interface or a hosted service.

No credentials, account identifiers, conversation IDs, or private session records
are included in this repository.
