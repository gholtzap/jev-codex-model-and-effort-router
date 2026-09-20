## Pre-reqs
- macos
- Jev api key

## Quick Setup

Copy and paste this into your coding agent:

> Set up and verify https://github.com/gholtzap/jev-codex-model-and-effort-router with default settings.

If no key is available, the first `codex` start lets the user add one, continue with normal Codex, or remove Jev. Key input is hidden, validated, and saved in a user-only file. Run `jev-codex auth login` to replace the saved key.
## Setup Options

| Option | Purpose |
| --- | --- |
| `--no-wrap-codex` | Install `jev-codex` without replacing the `codex` command. |
| `--no-shell` | Do not update shell startup files. |
| `--codex-path PATH` | Use a specific Codex executable. |
| `--env-file PATH` | Read `JEV_API_KEY` from a file. |

## What does it do?

[Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) selects the model and effort for the first task in a Codex thread. That route stays pinned for later turns so the thread can reuse its model cache. Greetings and other social messages do not set the pin.

In the default thread mode, manual model and effort changes in Codex take effect and become the new pin. Run `jev-codex auto on THREAD_ID` to let Jev select a new pin on the next turn.

The default `routing_mode` is `thread`. Set it to `turn` if you want Jev to select a route before every turn:

```sh
jev-codex config set routing_mode turn
```

## Menu Bar App

On macOS, run `jev-codex settings` to Launch the Menu Bar app.

You can use this app to choose the routing frequency, routing preference, maximum effort, and the model and effort pools from the menu bar.

<img width="374" height="250" alt="Screenshot 2026-09-19 at 11 57 45 AM" src="https://github.com/user-attachments/assets/4d6cd248-ddd4-4c89-b96e-d884b545dbc8" />
