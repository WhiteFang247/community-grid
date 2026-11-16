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

The approved source limit is 4,096 UTF-8 bytes of formatted prompt, including
ChatML roles, with a 2,048-token llama.cpp context and unchanged 64-token output
limit. This requires coordinated Dell deployment; source changes alone do not
change a running worker. The adapter does not silently discard Odysseus
instructions to fit the limit. If the actual prompt or context exceeds these
limits, stop for review rather than raising them automatically.

On 2026-11-16, the installed isolated Odysseus checkout was found at
`C:\Users\joris\Documents\Codex\2026-11-06\lets\work\odysseus-lenovo-stage8d`,
from `odysseus-dev/odysseus` at commit `934d23c0be29c9721385f34565c0ae2cbd60da04`.
Its data has an existing `Community Grid Distributed AI` provider at
`http://127.0.0.1:8975/v1`. It was preserved. Windows Smart App Control blocked
SQLAlchemy 2.1.8's unsigned `_cache_key_cy.cp314-win_amd64.pyd` from its
virtual environment. The PyPI wheel's published SHA-256 and the `.pyd` hash
matched a fresh download. SQLAlchemy's documented pure-Python source option
was used as an isolated import overlay from the SHA-256-verified 2.1.8 source
archive, leaving the original virtual environment and data intact. No Windows
security setting was changed. WSL was absent and was not installed.

The local Odysseus admin API added `Community Grid Dell Worker` as a second
provider at `http://127.0.0.1:8787/v1`, with tools disabled, the exact model
ID pinned, and admin-only visibility. An actual Odysseus `/api/chat` request
from session `7698137e-000d-4b11-a738-03e20f2d3d6a` returned "Two plus two
is four." Coordinator job `f2c13b2b-c17c-4eec-8317-995d3c67aaac` and an
independent authenticated Dell relay read matched the completed result and
7 output tokens. The Dell recorded 327 prompt tokens and 1,260,960 KiB peak
process RSS. This validates one remote worker request through Odysseus, not
distributed model execution.

The Lenovo user Startup folder launches a Python supervisor from this task's
`outputs` directory. It starts the API only if port 8787 is free, refuses to
replace an unrecognized listener, and restarts its child after exit. The API
remains on loopback. Stopping the prior Codex-owned API process caused the
supervisor to launch the updated adapter; the live API then accepted the
Odysseus request. This does not establish recovery after a Windows reboot.

Earlier validation: the Lenovo API completed job
`7fe48018-1fec-434a-b27a-9936e969ff48` with `Two plus two equals four.`;
the authenticated Dell relay independently returned the same completed job,
7 output tokens, and matching text.
