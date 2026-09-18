# Jev routing for Codex

A local terminal client that asks Jev to select a model and reasoning level before **each user message**. Codex App Server executes the work in one persistent thread. It uses your existing Codex login.

Requires Python 3.10 or later, macOS or Linux, an installed Codex CLI with App Server support, and a TypeSafe key. Tested with Codex CLI 0.155.0. No Python packages are required.

Give your coding agent this request:

> Set up https://github.com/gholtzap/jev-codex-model-and-effort-router

For manual setup:

```sh
git clone https://github.com/gholtzap/jev-codex-model-and-effort-router.git
cd jev-codex-model-and-effort-router
python3 install.py
```

## Start

Ask your agent to set up this repository. [AGENTS.md](AGENTS.md) gives it the
install, verification, settings, and removal steps. After cloning or downloading
the repository, one command installs it:

```sh
python3 install.py
```

The installer checks Codex sign-in and the TypeSafe connection. It reads an
existing `JEV_API_KEY` from the environment or `.env`, or asks for the key with
hidden input. For another key file, use `--env-file PATH`. Do not put the key in
a command argument. It stores the key separately with private file permissions.

Open a new terminal, enter your project directory, and run:

```sh
jev-codex
```

Balanced routing, a 10% allowance reserve, and usage display are on by default.
The client starts and stops its own Codex App Server. No idle background process,
network listener, Python packages, or administrator access are needed. The source
checkout can be moved or removed after installation.

To use `codex` as the routed command instead, install the optional wrapper:

```sh
python3 install.py --wrap-codex
```

This opens the Jev terminal interface when you run `codex`. `codex-original`
opens the standard Codex interface. Administrative commands such as `codex login`,
`codex app-server`, and `codex --version` still use the original executable.
`codex exec`, `review`, `resume`, and `fork` also retain native behaviour and do
not route through Jev. Use `jev-codex --resume-last` to resume a routed thread.
Router sessions support the options in `jev-codex --help`; this is not a complete
replacement for every native Codex option. The original executable is never changed.

Installation supports zsh and bash PATH setup. For other shells use `--no-shell`
and add `~/.local/bin` to PATH yourself. A new terminal loads the PATH change;
in the existing terminal, use `~/.local/bin/jev-codex`. Existing aliases or shell
functions named `codex` take precedence and must be removed by their owner if
the wrapper is wanted. `--codex-path PATH` selects the original binary explicitly.

To run directly from the checkout without installing:

```sh
./jev-codex -C /path/to/your/project
```

Enter a message and press Enter. Jev chooses from the models and reasoning levels advertised by your Codex installation. Codex streams its reply and runs its normal tools. Each following message gets a new routing decision with recent conversation context.

- `/paste`: enter several lines; end with a line containing only `.`.
- `/quit`: exit.
- `/usage`: read and display the current account allowance.
- `/settings`: show the effective settings for this session.
- Ctrl-C during execution: request a turn interruption, then return to the prompt.
- Ctrl-C again: close the client.

Resume after exit:

```sh
./jev-codex -C /path/to/your/project --resume-last
./jev-codex -C /path/to/your/project --resume THREAD_ID
```

The directory must match the saved thread. Router clients lock the thread while it is in use. Do not edit the same thread concurrently through another Codex client.

Run one message, or inspect a routing decision without executing it:

```sh
./jev-codex -C /path/to/your/project 'Fix the failing parser test'
./jev-codex --route-only 'Explain the difference between a list and a tuple'
./jev-codex --models
```

## Model selection

The default policy uses current model descriptions and supported reasoning levels from `model/list`. It is a capability-based starting policy, not a trained or measured optimizer. Jev confidence measures its distribution over the supplied choices; it is not the probability that the task will succeed.

Restrict the candidate models:

```sh
./jev-codex --allow-model gpt-5.6-luna --allow-model gpt-6-astra
```

For your own tested routing criteria, supply `--policy routes.json`. The format is a JSON object keyed by route name:

```json
{
  "routine": {
    "model": "gpt-5.6-luna",
    "effort": "low",
    "description": "Narrow edits, simple questions, and straightforward commands."
  },
  "complex": {
    "model": "gpt-6-astra",
    "effort": "high",
    "description": "Complex debugging and changes requiring careful reasoning across components."
  }
}
```

These model names are examples verified in the local catalog at implementation time. The client rejects models and effort values absent from your live catalog. Choose up to 255 routes. `--jev-model` selects the TypeSafe model; the default is `jev-latest`.

## Change saved settings

Ask your agent: "Set Jev Codex to conserve and keep 15% in reserve."
New routed sessions include instructions for the agent to use the settings CLI.
You can also use it directly:

```sh
jev-codex config show
jev-codex config set usage_policy conserve
jev-codex config set reserve_percent 15
jev-codex config set show_usage false
jev-codex config set allow_model '["gpt-5.6-luna", "gpt-6-astra"]'
jev-codex config set allow_model null
```

`config path` shows the saved file. Settings are validated and written atomically;
they reload before each message, stage, or repair. An active turn keeps the
settings it started with. Explicit command-line flags override saved settings for
that session. `--config PATH` selects a separate settings file; use
`jev-codex config --file PATH set NAME VALUE` to edit it.

Saved settings are `usage_policy`, `reserve_percent`, `show_usage`, `usage_limit`,
`allow_model`, and `jev_model`. Check commands and automatic repair limits stay
explicit per-run options. Invalid settings stop the next turn with an error.

## Check, update, and remove

```sh
jev-codex doctor
jev-codex doctor --offline
jev-codex uninstall
```

`doctor` checks Codex login, model access, usage data, and a small TypeSafe request.
The offline check only validates local settings, the original executable, and
the presence of a key; it cannot validate network access or credentials.

For an update, run `python3 install.py` from a new version of the checkout. Saved
settings and an enabled wrapper are kept. An atomic link selects the new code;
restart existing clients to use it. Reinstall if the recorded Python or original
Codex executable has moved; `--codex-path` can select a new Codex location.

Uninstall removes only managed launchers and PATH entries. Changed launchers are
preserved and reported. Settings and the key remain unless you use
`jev-codex uninstall --purge`. Codex conversations and routing records are kept
in both cases. There is no service to stop. Already open sessions can finish;
close them before removal if you want routing to stop immediately.

Default locations:

- Commands: `~/.local/bin`.
- Settings and key: `~/.config/jev-codex` (`XDG_CONFIG_HOME` is supported).
- Installed code: `~/.local/share/jev-codex` (`XDG_DATA_HOME` is supported).
- Routing records: `~/.local/state/jev-codex`.

## Usage-based selection

```sh
./jev-codex --usage
./jev-codex -C /path/to/your/project --usage-policy balanced
./jev-codex -C /path/to/your/project --usage-policy conserve --reserve-percent 15
./jev-codex --show-usage
```

Use `--usage-policy quality` to exclude usage from selection. `--no-show-usage`
also hides the display in quality mode. The default is `balanced`. `balanced` and `conserve` both
display the allowance and send a usage preference to Jev before every turn,
including repair turns and task stages. `--usage` reads the allowance and exits;
it does not need a Jev key or start a model turn.

The router reads `account/rateLimits/read` before each decision. It does not
interrupt an active turn when usage changes. The next decision uses a fresh
snapshot, including usage from other Codex sessions.

The default limit bucket is `codex`. Use `--usage-limit ID` if your account uses
a different bucket. Both returned windows in the selected bucket are checked;
unrelated model or account buckets are not combined. The pacing calculation is:

```text
usable percentage = max(0, remaining percentage - reserve percentage)
time fraction = min(1, seconds until reset / window duration in seconds)
pressure = clamp(1 - usable percentage / (100 * time fraction), 0, 1)
```

The larger window pressure applies. `conserve` sets a minimum pressure of 0.5.
Below 0.25 the preference is normal; from 0.25 to below 0.65 it is economical;
at 0.65 or above it is strongly economical. The default reserve is 10 percentage
points. The display also shows usable percentage points per day until reset.
These are initial policy thresholds, not measured consumption rates.

Jev uses that preference with the live model descriptions. Difficult work and
failed verification can still select stronger models. The reserve is a planning
buffer, not a hard spending cap. The code calculates the pressure; Jev makes the
final model choice. The account API does not supply per-model usage prices, so
the router does not promise that a smaller model will consume less allowance.

For `balanced` and `conserve`, missing, invalid, expired, or blocked usage data
stops the run before a new turn. Display-only mode reports unavailable data and
can continue. Usage records contain window data, not account IDs or credit balances.

## Checks, repairs, and stages

```sh
./jev-codex -C /path/to/your/project --usage-policy balanced \
  --check-command 'python3 -m unittest' --max-retries 1 'Fix the parser'
```

After a completed turn, the client runs your check in the project directory.
The command runs locally with your user permissions, outside the Codex sandbox.
Use a trusted check. Arguments support quotes, but there is no shell expansion,
pipeline, or redirection. Use an explicit script for several commands.

A nonzero check exit code can start a repair turn in the same conversation.
Jev makes a new decision with the failure as context. The default repair limit
is zero; `--max-retries` sets a limit per user message or stage. A failed check
after that limit exits with status 1 in single-message or staged mode.
Interrupted or failed Codex turns, check timeouts, and check launch errors do
not trigger automatic retries. `--check-timeout` defaults to 300 seconds.

The last 8,000 bytes of failed check output are included in the repair message,
which goes to Codex and Jev. The routing audit records check status and duration
without copying that output. Full check output uses a temporary disk file that
is removed after the check. Keep secrets out of check output.

To select a model between known work stages, provide a JSON array of messages:

```json
[
  "Investigate the parser failure and explain the cause. Do not edit files yet.",
  "Implement the repair from the previous stage and verify it."
]
```

```sh
./jev-codex -C /path/to/your/project --stages stages.json --usage-policy balanced
```

Each stage runs as a new turn in the same conversation. There is no automatic
task splitting. An optional check runs after each stage and each repair; a
failed check must pass before the next stage starts.

## Execution and errors

The default is `workspace-write` with `on-request` approvals handled by the user. Use `--sandbox read-only` for read-only work. Codex also applies its configuration and organization policy. The router does not grant permission based on a Jev decision.

Command, file, and permission requests require explicit terminal input. Unsupported client requests stop the client with an error. A non-interactive run that requires user input stops; resume the saved thread in an interactive terminal. MCP form input accepts JSON matching the schema displayed by the server.

TypeSafe rate-limit and temporary server errors get at most two retries. Invalid decisions and other routing errors stop before starting a Codex turn. There is no silent model fallback. Codex service reroutes are displayed and recorded.

A failed Codex turn exits with a nonzero status in single-message mode. Interactive mode lets you send a follow-up. `--turn-timeout` sets the maximum execution time in seconds (default 1800); expiry requests cancellation. A transport failure closes the local server. The thread ID is saved before execution so you can inspect or resume it after a failure.

## Data and records

Jev receives the current message and up to 16,000 trailing characters of user/assistant history, plus tool completion status and exit codes. Truncation is disclosed in the routing state. Raw tool output and repository files are not automatically copied into this state. Content quoted in conversation can still contain project data. Codex retains its full thread independently of this routing context limit.

The client supports text input. It does not implement the full Codex terminal UI, image attachments, or automatic switching inside a running turn. This is a local App Server client; it does not expose a hosted HTTP endpoint.

Router records are in `~/.local/state/jev-codex`, or `--state-dir PATH`. Session pointers and JSONL records are written with private file permissions. Records include decision IDs, selected models, all route probabilities, Jev token usage, routing time, Codex token usage when reported, turn status, and service reroutes. Prompt text is not duplicated in the routing log; Codex stores the conversation through its normal session storage.

No dollar savings are reported. ChatGPT subscription limits and API pricing differ, and usage counts alone do not establish cost savings or task quality. A completed turn means the agent finished; it does not establish that the work is correct. Use task checks to evaluate routing quality.

## Verification

```sh
python3 -m unittest -v
python3 demo.py
```

Tests cover invalid routes, key loading, history bounds, RPC event interleaving, server requests, disconnects, streaming, cancellation, and approval decisions. Live verification is recorded in `VERIFICATION.md`.

Protocol sources: [Codex App Server](https://learn.chatgpt.com/docs/app-server), [TypeSafe HTTP API](https://docs.typesafe.ai/api).
