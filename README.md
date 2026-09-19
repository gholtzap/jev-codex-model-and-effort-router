## Quick Setup

Copy and paste this into your coding agent:

> Set up and verify https://github.com/gholtzap/jev-codex-model-and-effort-router with default settings.

The agent should clone the repository and run `python3 install.py`. The installer securely asks for a TypeSafe API key when needed, verifies Codex sign-in and both service connections, and configures the standard `codex` command to route through Jev. It keeps the original command available as `codex-original`.

Use `python3 install.py --no-wrap-codex` only when you want a separate `jev-codex` command without changing how `codex` starts.

## What does it do?

Every time you send a message on Codex, [Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) selects the model and effort to match the complexity of your request.

On macOS, run `jev-codex settings` to choose a routing preference, maximum effort, and the model and effort pools from the menu bar. The router reloads them before each turn.
