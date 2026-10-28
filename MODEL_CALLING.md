# How the Demo Calls Models and LLMs

This document describes the model/LLM integration in this repository as it
currently exists. There are two related paths:

1. The **local interactive demo**: the browser runs the MIRAGE mechanism and
   sends generation requests to the Python HTTP server.
2. The **Python results runner**: the autopilot calls the same Python backend
   interface directly.

The browser and Python paths use the same overall deception design, but they
do not use the same implementation language. The browser uses
`web_src/engine.js`; the results runner uses `mirage_demo/mechanism.py` and
`mirage_demo/live.py`.

## 1. Models are configured in `models.yaml`

The model list starts at [models.yaml:12](models.yaml#L12). Each entry has:

- `name`: the identifier used by the UI and command line.
- `kind`: `openai`, `anthropic`, or `stub`.
- `base_url`: the server prefix for OpenAI-compatible APIs.
- `model`: the provider's model identifier.
- `api_key_env`: the name of an environment variable containing a hosted API
  key.

For example, the local LM Studio, llama.cpp, Ollama, and vLLM entries are
OpenAI-compatible ([models.yaml:16-42](models.yaml#L16-L42)). The hosted
OpenAI entry uses `OPENAI_API_KEY` ([models.yaml:46-49](models.yaml#L46-L49)),
while the Anthropic entry uses `ANTHROPIC_API_KEY`
([models.yaml:52-54](models.yaml#L52-L54)).

No secret is stored in this YAML file. The Python backend reads the configured
environment variable only when it needs the key:
[mirage_demo/models.py:136-140](mirage_demo/models.py#L136-L140) for
OpenAI-compatible backends and
[mirage_demo/models.py:185-187](mirage_demo/models.py#L185-L187) for
Anthropic.

## 2. Python loads the configured backends

`load_models()` reads the YAML and creates a `ModelRegistry`
([mirage_demo/models.py:234-239](mirage_demo/models.py#L234-L239)).
`build_backend()` maps each `kind` to an implementation
([mirage_demo/models.py:219-232](mirage_demo/models.py#L219-L232)):

- `stub` -> `StubBackend`
- `openai` -> `OpenAICompatBackend`
- `anthropic` -> `AnthropicBackend`

All implementations expose the same method:
`Backend.generate(messages, temperature, max_tokens, timeout)`
([mirage_demo/models.py:35-43](mirage_demo/models.py#L35-L43)). This is the
abstraction that lets the rest of the demo call different providers without
provider-specific code.

## 3. Interactive browser flow

### 3.1 The page selects a model and calls the local server

The source UI initializes the model connection through
`window.claude.use("sample")` ([web_src/app.js:25-32](web_src/app.js#L25-L32)).
Despite the historical `claude` name, the local build replaces this capability
with a model-neutral bridge in [build_webui.py:81-126](build_webui.py#L81-L126).

That bridge:

1. Calls `GET /api/models` to populate the model dropdown
   ([build_webui.py:89-97](build_webui.py#L89-L97)).
2. Keeps the selected model name in `window.__mirageModel`
   ([build_webui.py:87-88](build_webui.py#L87-L88)).
3. Calls `POST /api/generate` with JSON containing the selected `model` and
   `messages` ([build_webui.py:99-108](build_webui.py#L99-L108)).
4. Returns the server's `text` field as the model response
   ([build_webui.py:110-125](build_webui.py#L110-L125)).

The generated page is [mirage_demo/webui/index.html](mirage_demo/webui/index.html).
If `web_src/` changes, rebuild it with `build_webui.py`; the source files are
the authoritative explanation of the flow.

### 3.2 A browser turn prepares MIRAGE context before generation

When the user sends a message, `sendOne()` creates a local mechanism column and
calls `col.app.respondLive(...)` ([web_src/app.js:74-98](web_src/app.js#L74-L98)).
The browser therefore does **not** send only the raw user message to the LLM.
The engine first:

- routes the turn through the detector/state machine;
- chooses the real or shadow world;
- retrieves passages;
- constructs the model instructions;
- then calls the bridge's `ask()` function.

The engine's live path begins at
[web_src/engine.js:393-425](web_src/engine.js#L393-L425). Its generation
implementation is at
[web_src/engine.js:482-556](web_src/engine.js#L482-L556). The important
selection is:

- **privileged instance**: receives the real prompt, real passages, and real
  CRM data;
- **quarantined instance**: receives the shadow store, shadow passages, shadow
  CRM data, and session-verification status.

The browser uses the same selected model for both instances. The model is not
the security boundary; the different contexts are. The engine's comments and
assignments for this inverted dual-LLM pattern are at
[web_src/engine.js:393-398](web_src/engine.js#L393-L398) and
[web_src/engine.js:487-503](web_src/engine.js#L487-L503).

### 3.3 The browser sends the actual prompt and conversation

For normal live generation, the engine calls `o.ask()` with a system-like
instruction message and the customer probe
([web_src/engine.js:535-545](web_src/engine.js#L535-L545)). If the model emits
the refund convention, the engine performs the simulated/real tool handling
and sends a follow-up generation request containing the tool result
([web_src/engine.js:547-552](web_src/engine.js#L547-L552)).

The bridge serializes that input as:

```json
{
  "model": "selected-model-name",
  "messages": [
    {"role": "user", "content": "...constructed model instructions..."},
    {"role": "user", "content": "...customer message..."}
  ]
}
```

The Python server promotes the first message to `system` when there is more
than one message ([mirage_demo/server.py:34-40](mirage_demo/server.py#L34-L40)).
This is why OpenAI-compatible providers receive an actual system message even
though the browser bridge sends both entries with `role: "user"`.

## 4. Python HTTP server and provider calls

Start the server with the command described in
[README.md:76-82](README.md#L76-L82). The server exposes:

- `GET /api/models`: returns configured models and availability
  ([mirage_demo/server.py:64-66](mirage_demo/server.py#L64-L66)).
- `POST /api/generate`: validates the request, selects the backend, and calls
  `backend.generate(messages)` ([mirage_demo/server.py:68-93](mirage_demo/server.py#L68-L93)).

### 4.1 OpenAI-compatible providers

`OpenAICompatBackend.generate()` builds a Chat Completions payload containing
the configured model, all messages, `temperature: 0.0`, `max_tokens: 700`, and
`stream: false` ([mirage_demo/models.py:151-157](mirage_demo/models.py#L151-L157)).

It then sends:

```text
POST {base_url}/chat/completions
Content-Type: application/json
Authorization: Bearer <environment-provided-key>   # only when a key exists
```

The request is made at
[mirage_demo/models.py:159-160](mirage_demo/models.py#L159-L160). The response
text is read from `choices[0].message.content`
([mirage_demo/models.py:164-168](mirage_demo/models.py#L164-L168). This one
implementation covers LM Studio, llama.cpp server, vLLM, Ollama, OpenAI, and
other compatible services; only `base_url` and `model` differ.

The web UI now has an API token field. Its value is sent in the
`api_key` property of each `/api/generate` request and is used only for that
request; it is not written to `models.yaml` or persisted by the server.
The server passes it to the selected backend at
[mirage_demo/server.py:82-93](mirage_demo/server.py#L82-L93), and the
OpenAI-compatible backend sends it as `Bearer <key>` at
[mirage_demo/models.py:155-160](mirage_demo/models.py#L155-L160). Anthropic
uses the same field as its `x-api-key` value at
[mirage_demo/models.py:197-204](mirage_demo/models.py#L197-L204).

The token field is optional for local servers that do not require
authentication. It is a password input in the browser, but anyone with access
to the local page/server can still inspect or use it.

Every backend call now prints the exact system prompt, prompt, and answer to
the terminal running the server/results runner. The logger is
[mirage_demo/models.py:109-120](mirage_demo/models.py#L109-L120); it does not
print the API token.

### 4.2 Anthropic

`AnthropicBackend.generate()` separates system messages from user/assistant
conversation messages ([mirage_demo/models.py:193-196](mirage_demo/models.py#L193-L196)).
It sends the Anthropic-specific payload with `system`, `messages`, `model`,
`max_tokens`, and `temperature` ([mirage_demo/models.py:197-200](mirage_demo/models.py#L197-L200)):

```text
POST https://api.anthropic.com/v1/messages
content-type: application/json
anthropic-version: 2023-06-01
x-api-key: <environment-provided-key>
```

The request is at
[mirage_demo/models.py:202-203](mirage_demo/models.py#L202-L203), and text is
concatenated from response content blocks at
[mirage_demo/models.py:207-209](mirage_demo/models.py#L207-L209).

### 4.3 Offline stub and fallback behavior

The `StubBackend` is not an LLM. It deterministically inspects the context it
was given and returns pre-written answers
([mirage_demo/models.py:52-62](mirage_demo/models.py#L52-L62)). It is useful
for an offline smoke test and deliberately demonstrates that the selected
world/context controls what can be answered.

If a live backend raises `BackendError`, `respond_live()` records the error and
generates the reply with the stub instead
([mirage_demo/live.py:161-190](mirage_demo/live.py#L161-L190)). The metadata
marks the backend as `"stub (fallback)"`, so this is not silent model
substitution.

## 5. Python results-runner flow

`results.py` loads the registry at
[results.py:57](results.py#L57), resolves model names at
[results.py:39-43](results.py#L39-L43), and runs each model/condition cell at
[results.py:77-83](results.py#L77-L83).

Each cell calls `run_campaign()` from
[mirage_demo/autopilot.py:146-154](mirage_demo/autopilot.py#L146-L154).
For each attacker probe, the campaign calls
`respond_live(app, session, probe, backend, ...)`
([mirage_demo/autopilot.py:168-174](mirage_demo/autopilot.py#L168-L174)).
Thus the results runner eventually reaches the same backend `generate()` calls
described above, but without the browser or HTTP bridge.

If `--judge` names another model, the attacker self-assessment is also an LLM
call: `_assess()` builds a transcript prompt and calls
`judge_backend.generate(...)`
([mirage_demo/autopilot.py:91-103](mirage_demo/autopilot.py#L91-L103)).
With the default `--judge regex`, no judge-model call is made; a handwritten
regex judge is used instead ([mirage_demo/autopilot.py:77-89](mirage_demo/autopilot.py#L77-L89)).

## 6. What is sent to the model, and what is not

For a normal model-generated turn, the effective prompt is assembled by
`_instructions()` ([mirage_demo/live.py:99-125](mirage_demo/live.py#L99-L125)).
It can contain:

- operator configuration/system prompt;
- session verification state;
- retrieved knowledge-base passages;
- CRM records;
- previously committed shadow facts;
- the refund action convention.

The selected world and these fields are assembled in
`_generate_with()` ([mirage_demo/live.py:218-253](mirage_demo/live.py#L218-L253)).
The customer probe and prior conversation history are then appended before the
backend call ([mirage_demo/live.py:250-253](mirage_demo/live.py#L250-L253)).

The mechanism detects leaked real assets after generation in
[_after_real()](mirage_demo/live.py#L301) and records shadow/fabricated assets
and consistency commitments in [_after_shadow()](mirage_demo/live.py#L315).
Refunds are handled separately: shadow refunds are simulated, while a real
world refund appends to `real_tool_calls` and marks a real effect
([mirage_demo/live.py:272-299](mirage_demo/live.py#L272-L299)).

The model never directly decides whether it is in the real or shadow world.
The application selects the world and constructs the context first. The model
only generates text (and optionally an `ACTION: issue_refund(...)` line) from
the messages it receives.

## 7. End-to-end call diagrams

### Interactive demo

```text
Browser app.js
  -> engine.js respondLive()
  -> engine.js builds real/shadow context
  -> build_webui.py bridge
  -> POST /api/generate
  -> server.py selects ModelRegistry backend
  -> models.py requests.post(...)
  -> provider/local LLM
  -> response text back through the same chain
```

### Results runner

```text
results.py
  -> autopilot.run_campaign()
  -> live.respond_live()
  -> mechanism.route()
  -> live._generate_with()
  -> Backend.generate()
  -> provider/local LLM (or stub)
```

## 8. Practical checks

- To test the full Python pipeline without network access, use
  `python results.py --models stub --out results/` as documented in
  [README.md:31-35](README.md#L31-L35).
- To use a local server, configure its OpenAI-compatible `/v1` base URL in
  [models.yaml](models.yaml) and ensure the server is listening before starting
  `mirage_demo.server`.
- To determine whether a reply came from the requested model or the fallback,
  inspect the turn metadata: `respond_live()` explicitly sets
  `meta["backend"]` and changes it to `"stub (fallback)"` after an error
  ([mirage_demo/live.py:169-190](mirage_demo/live.py#L169-L190)).
