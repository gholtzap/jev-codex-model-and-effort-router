# Routing APIs

The routing API selects the Codex model and reasoning effort. Codex still runs
the coding task. Jev remains the default routing API.

Native decision APIs are available as **Jev** and **OpenAI Decisions**. The latter
calls `POST /v1/decisions` with named choice questions. It uses the API choice,
confidence, and probabilities directly. It does not generate these fields with
a chat prompt. The default decision model is `gpt-6-luna`, the model supported
by the OpenAI Decisions API at the time of this change. You can set another
model ID when your endpoint supports it.

To use OpenAI Decisions:

```sh
jev-codex config set routing_provider openai_decisions
jev-codex auth login
```

This uses `https://api.openai.com/v1` and `OPENAI_API_KEY` by default. The
endpoint and key-variable settings also apply to this API.

Each native choice question supports at most 255 options. If only one route is
allowed, that route is fixed without asking the API to choose it. Other routing
questions still go to the API.

Reference: [OpenAI Decisions](https://developers.openai.com/api/docs/guides/decisions).

## OpenRouter Decisions

Select **OpenRouter Decisions** in the Routing API menu, or run:

```sh
jev-codex config set routing_provider openrouter
jev-codex config set routing_model cloudflare/clef-flash
jev-codex auth login
```

This calls `https://openrouter.ai/api/alpha/decisions` with native decision
questions. It uses `OPENROUTER_API_KEY`, separate from Codex sign-in and other
provider keys. If no routing model is set, it uses `typesafe/jev-1.14`.

You can enter any decision-model ID supported by OpenRouter. The router has no
fixed list of model IDs. Find current models in the
[OpenRouter decision catalog](https://openrouter.ai/models?output_modalities=decisions),
or use the public catalog API:

```sh
curl 'https://openrouter.ai/api/v1/models?output_modalities=decisions'
```

The adapter uses the native choice, confidence, and probability fields. Missing
answers, unsupported routes, and invalid probabilities stop the request. The
router does not change to another routing model after an API error. OpenRouter
can select an available provider for the model you request under its own service
rules.

Reference: [OpenRouter Decisions API](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request).

The separate **Chat Completions-compatible** option uses Chat Completions with a strict JSON Schema
response format. Select any model that supports this format at your endpoint.
The router does not maintain a fixed list of routing models. The selected route
must still be in the live Codex model and effort pool.

## Settings

Use `jev-codex settings` on macOS. Select **Routing API**, then set **Routing
model**. The OpenAI and OpenRouter options also show **Routing endpoint** and
**API key variable**. Enter a variable name in the last field, not a secret key.

You can also use the CLI:

```sh
jev-codex config set routing_provider openai
jev-codex config set routing_model your-model-id
jev-codex auth login
```

The default endpoint is `https://api.openai.com/v1`. The default key variable
is `OPENAI_API_KEY`. Authentication for this API is separate from Codex sign-in.
For API-key authentication, `auth login` reads a key without displaying it, checks the selected API, and
saves it in the private credentials file. Keys for other providers are kept.
An environment variable with the selected name takes precedence over that file.

For another compatible API:

```sh
jev-codex config set routing_base_url https://your-api.example/v1
jev-codex config set routing_api_key_env CUSTOM_ROUTER_KEY
jev-codex auth login
```

A custom endpoint requires an explicit key-variable selection. This prevents
sending the default OpenAI key to a different host by mistake. To use a local
API without authentication, set the variable name to an empty string:

```sh
jev-codex config set routing_base_url http://127.0.0.1:11434/v1
jev-codex config set routing_api_key_env '""'
```

HTTP is allowed only on loopback. Endpoint URLs cannot contain credentials,
queries, or fragments. Requests do not follow redirects.

To return to Jev:

```sh
jev-codex config set routing_provider jev
```

Changing providers clears the routing-model override and key-variable override,
and resets the endpoint to the new provider default. This also applies to a
`--routing-provider` override; explicit endpoint, model, and key-variable overrides
still take precedence.
Jev then uses the existing `jev_model` setting, which defaults to `jev-latest`,
and `JEV_API_KEY`. Existing configurations continue to work. `routing_model`
can override the model for any routing API. CLI routing commands also support
`--routing-provider`, `--routing-model`, `--routing-base-url`, and
`--routing-api-key-env`.

The selected API receives the request and recent conversation context. Provider
errors, refusals, incomplete answers, and unsupported routes stop the routing
request. The router does not fall back to another provider. Reported confidence
and probabilities are provider estimates, not calibrated guarantees. Existing
capability, effort, and quota rules still apply after the API response.

Changes apply at the next route selection. In once-per-thread mode, an existing
route stays pinned. Use `jev-codex auto on THREAD_ID` to request a new selection,
or select every-turn routing.

API format reference: [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

Start a new Codex session to use a provider added by an installed update.
Existing sessions keep the router code they started with.
