## Pre-reqs
- macos
- Jev api key

## Quick Setup

Copy and paste this into your coding agent:

> Set up and verify https://github.com/gholtzap/jev-codex-model-and-effort-router with default settings.

## Setup Options

| Option | Purpose |
| --- | --- |
| `--no-wrap-codex` | Install `jev-codex` without replacing the `codex` command. |
| `--no-shell` | Do not update shell startup files. |
| `--codex-path PATH` | Use a specific Codex executable. |
| `--env-file PATH` | Read `JEV_API_KEY` from a file. |

## What does it do?

Every time you send a message on Codex, [Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) selects the model and effort to match the complexity of your request.

## Menu Bar App

On macOS, run `jev-codex settings` to Launch the Menu Bar app.

You can use this app to choose a routing preference, maximum effort, and the model and effort pools from the menu bar.

<img width="374" height="250" alt="Screenshot 2026-09-19 at 11 57 45 AM" src="https://github.com/user-attachments/assets/4d6cd248-ddd4-4c89-b96e-d884b545dbc8" />

