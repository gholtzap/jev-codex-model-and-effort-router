# Routing APIs

The routing API selects the Codex model and reasoning effort. Codex still runs
the coding task. Jev remains the default routing API.

OpenAI-compatible routing APIs use Chat Completions with a strict JSON Schema
response format. Select any model that supports this format at your endpoint.
The router does not maintain a fixed list of routing models. The selected route
must still be in the live Codex model and effort pool.

## Settings

Use `jev-codex settings` on macOS. Select **Routing API**, then set **Routing
model**. The OpenAI-compatible option also shows **Routing endpoint** and
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

Changing providers clears the routing-model override and key-variable override.
Jev then uses the existing `jev_model` setting, which defaults to `jev-latest`,
and `JEV_API_KEY`. Existing configurations continue to work. `routing_model`
can override the model for either API. CLI routing commands also support
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
