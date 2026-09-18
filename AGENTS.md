# Agent setup and operation

This repository supplies a local Codex terminal client. When the user asks you
to set up this repository, complete the following steps.

1. Use Python 3.10 or later on macOS or Linux. Check `codex login status`.
   If Codex is missing or sign-in is required, help the user complete that step.
2. Run `python3 install.py` from this repository. It verifies Codex and TypeSafe,
   installs a private copy, and adds `~/.local/bin` to zsh or bash startup files.
   No Python packages, administrator access, or idle service are required.
3. The installer reads `JEV_API_KEY` from the environment, saved credentials,
   or the repository `.env`. Use `--env-file PATH` for another existing file.
   If no key exists, ask the user to enter it through the installer's hidden
   terminal prompt. Never print a key or include it in a command argument.
4. Run `~/.local/bin/jev-codex doctor`. It performs a small TypeSafe API request
   and checks the Codex connection. Do not claim setup passed if this fails.
5. Tell the user to open a new terminal, enter their project directory, and run
   `jev-codex`. Defaults are balanced routing, a 10% reserve, and usage display.

If the user requests use of the `codex` command for routing, run
`python3 install.py --wrap-codex`. Explain that this opens the Jev terminal
client. `codex-original` keeps the standard Codex interface available. Native
administrative subcommands such as `codex login` and `codex app-server` still
go to the original binary. Do not replace the original binary or add an alias.

For an update, run the installer from the new checkout. It keeps saved settings
and uses an atomic link to switch the installed source. Restart open clients to
use new code. Settings changes do not require a restart.

## Settings requests

When the user asks to change routing behaviour, use the installed CLI:

```sh
jev-codex config show
jev-codex config set usage_policy conserve
jev-codex config set reserve_percent 15
jev-codex config set show_usage false
jev-codex config set allow_model '["gpt-5.6-luna", "gpt-6-astra"]'
jev-codex config set allow_model null
```

Use `jev-codex --models` to check available model names before setting a model
list. Setting names also accept hyphens. `config path` reports the file location.
Read settings back after a change. Open sessions reload settings before the next
turn. Explicit command-line options take priority for that session. Invalid
settings stop the next turn; they do not silently select a different policy.

The reserve is a planning buffer, not a spending cap. Usage preferences do not
guarantee savings. Model changes happen between turns. Automatic repairs require
an explicit check command and retry limit; do not enable them globally.

## Removal and maintenance

`jev-codex uninstall` removes installed commands and managed PATH entries.
`jev-codex uninstall --purge` also removes saved settings and the saved API key.
Both keep Codex conversations and routing audit records. Uninstall refuses to
delete commands that have been changed by another tool or the user.

When changing this code, inspect existing functions first. Use the shared
settings validation, private file writes, HTTP client, and App Server transport.
Run `python3 -m unittest -v`. Do not publish keys, `.env`, local account data,
or machine-specific verification records. Use simple technical English.
