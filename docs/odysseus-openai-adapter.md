# Lenovo OpenAI-compatible adapter for the Dell worker

The Lenovo serves `http://127.0.0.1:8787/v1`. Its only advertised model is
`community-grid-qwen2.5-1.5b-instruct-q4_k_m`. Start it from this checkout with
`PYTHONPATH=runtime` and:

```
python -m community_grid.openai_gateway --config PATH_TO_PINNED_PEER_JSON --state PATH_TO_PRIVATE_STATE_JSON
```

The peer JSON must contain exactly one worker with `forced_command: true` and
use the existing pinned OpenSSH profile. The adapter calls the existing
coordinator and SSH transport. The Dell service stays on `127.0.0.1:8767`;
no Dell LAN listener or new credentials are required. The adapter deliberately
rejects every bind address except Lenovo loopback. It has no remote client
authentication, so do not expose or forward port 8787 to another device.

`GET /v1/models` and `POST /v1/chat/completions` return OpenAI-compatible
JSON. Non-streaming is supported; a bounded `stream: true` SSE response is
also available for Odysseus. SSE emits the result after the Dell job finishes,
so it does not provide live token streaming. `X-Community-Grid-Job-ID` identifies
the Dell execution record. Error responses use an `error` object and reject
unknown models, tools, unsupported fields, malformed messages, overlong input,
and concurrent requests. The worker runs deterministic inference, limited to
64 output tokens. A supplied temperature is validated but does not alter that
fixed worker setting. Odysseus's local `session_id` and `cache_prompt` hints
are accepted but do not create cross-job KV cache affinity.

The current worker/coordinator limit is 512 UTF-8 bytes of formatted prompt,
including ChatML roles. Odysseus's normal agent prompt previously measured
about 833 model tokens, so an actual Odysseus conversation may exceed the
current limit. Raising it requires coordinated changes and a separately
approved Dell service deployment, plus a context-window check. This adapter
does not silently discard Odysseus instructions to fit the limit.

On 2026-11-16, the installed isolated Odysseus checkout was found at
`C:\Users\joris\Documents\Codex\2026-11-06\lets\work\odysseus-lenovo-stage8d`,
from `odysseus-dev/odysseus` at commit `934d23c0be29c9721385f34565c0ae2cbd60da04`.
Its data has an existing `Community Grid Distributed AI` provider at
`http://127.0.0.1:8975/v1`. It was preserved. The app could not start because
Windows Smart App Control blocked a compiled dependency in its Python virtual
environment (`sqlalchemy.sql._util_cy`). No new provider or Odysseus chat has
been claimed. Do not turn off Smart App Control as a workaround; use an
approved, trusted Odysseus runtime before continuing the admin-API setup.

Validation: the Lenovo API completed job
`7fe48018-1fec-434a-b27a-9936e969ff48` with `Two plus two equals four.`;
the authenticated Dell relay independently returned the same completed job,
7 output tokens, and matching text. This validates one remote-worker inference
request, not distributed model execution or Odysseus integration.
