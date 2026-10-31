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
OpenAI-compatible ([models.yaml:15-42](models.yaml#L15-L42)). The hosted
OpenAI entry uses `OPENAI_API_KEY` ([models.yaml:45-49](models.yaml#L45-L49)),
while the Anthropic entry uses `ANTHROPIC_API_KEY`
([models.yaml:51-54](models.yaml#L51-L54)).

No secret is stored in this YAML file. The Python backend reads the configured
environment variable only when it needs the key:
[mirage_demo/models.py:162-169](mirage_demo/models.py#L162-L169) for
OpenAI-compatible backends and
[mirage_demo/models.py:216-219](mirage_demo/models.py#L216-L219) for
Anthropic. A token typed into the web UI takes precedence (§4.1).

## 2. Python loads the configured backends

`load_models()` reads the YAML and creates a `ModelRegistry`
([mirage_demo/models.py:268-273](mirage_demo/models.py#L268-L273)).
`build_backend()` maps each `kind` to an implementation
([mirage_demo/models.py:253-265](mirage_demo/models.py#L253-L265)):

- `stub` -> `StubBackend`
- `openai` -> `OpenAICompatBackend`
- `anthropic` -> `AnthropicBackend`

All implementations expose the same method:
`Backend.generate(messages, temperature, max_tokens, timeout, api_key)`
([mirage_demo/models.py:36-43](mirage_demo/models.py#L36-L43)). This is the
abstraction that lets the rest of the demo call different providers without
provider-specific code.

## 3. Interactive browser flow

### 3.1 The page selects a model and calls the local server

The source UI initializes the model connection through
`window.claude.use("sample")` ([web_src/app.js:27-34](web_src/app.js#L27-L34)),
and every generation goes through its `ask()` wrapper
([web_src/app.js:43-48](web_src/app.js#L43-L48)).
Despite the historical `claude` name, the local build replaces this capability
with a model-neutral bridge in [build_webui.py:84-143](build_webui.py#L84-L143).

That bridge:

1. Calls `GET /api/models` to populate the model dropdown
   ([build_webui.py:93-101](build_webui.py#L93-L101)).
2. Keeps the selected model name in `window.__mirageModel`
   ([build_webui.py:92](build_webui.py#L92)).
3. Calls `POST /api/generate` with JSON containing the selected `model`, the
   optional `api_key` and the `messages`
   ([build_webui.py:103-116](build_webui.py#L103-L116)).
4. Returns the server's `text` field as the model response
   ([build_webui.py:118-126](build_webui.py#L118-L126)), or throws the server's
   `error` text if the request failed
   ([build_webui.py:114](build_webui.py#L114)).

The generated page is [mirage_demo/webui/index.html](mirage_demo/webui/index.html).
If `web_src/` changes, rebuild it with `build_webui.py`; the source files are
the authoritative explanation of the flow.

### 3.2 A browser turn prepares MIRAGE context before generation

When the user sends a message, `sendOne()` calls `col.app.respondLive(...)`
for each chat column, passing `ask`, the column's recent history and a
streaming callback ([web_src/app.js:76-105](web_src/app.js#L76-L105)).
The browser therefore does **not** send only the raw user message to the LLM.
The engine first:

- routes the turn through the detector/state machine;
- chooses the real or shadow world;
- retrieves passages;
- constructs the model instructions;
- then calls the bridge's `ask()` function.

The engine's live path begins at
[web_src/engine.js:425-450](web_src/engine.js#L425-L450). Its generation
implementation is at
[web_src/engine.js:542-649](web_src/engine.js#L542-L649). The important
selection is:

- **privileged instance**: receives the real prompt, real passages, and real
  CRM data;
- **quarantined instance**: receives the shadow store, shadow passages, shadow
  CRM data, and session-verification status.

The browser uses the same selected model for both instances. The model is not
the security boundary; the different contexts are. The engine's comments and
assignments for this inverted dual-LLM pattern are at
[web_src/engine.js:425-432](web_src/engine.js#L425-L432) and
[web_src/engine.js:553-583](web_src/engine.js#L553-L583). What the quarantined
instance is given before and after verification is described in §6.

### 3.3 The browser sends the actual prompt and conversation

For normal live generation, the engine calls `o.ask()` with a system-like
instruction message, the earlier turns of the session, and the customer probe
([web_src/engine.js:613](web_src/engine.js#L613),
[web_src/engine.js:632-633](web_src/engine.js#L632-L633)). If the model emits
the refund convention, the engine parses it, performs the simulated/real tool
handling and sends a follow-up generation request containing the tool result,
asking for one complete reply
([web_src/engine.js:634-644](web_src/engine.js#L634-L644)). Before the reply
is shown, and while it streams, the `ACTION:` line is removed and, in the
shadow world, the `{{CONFIGURATION}}` placeholder is replaced
([web_src/engine.js:615-619](web_src/engine.js#L615-L619),
[web_src/engine.js:646](web_src/engine.js#L646)).

The bridge serializes that input as:

```json
{
  "model": "selected-model-name",
  "api_key": "",
  "messages": [
    {"role": "user", "content": "...constructed model instructions..."},
    {"role": "user", "content": "...earlier customer message..."},
    {"role": "assistant", "content": "...earlier reply..."},
    {"role": "user", "content": "...customer message..."}
  ]
}
```

The Python server promotes the first message to `system` when there is more
than one message ([mirage_demo/server.py:34-41](mirage_demo/server.py#L34-L41)).
This is why OpenAI-compatible providers receive an actual system message even
though the browser bridge sends the instructions with `role: "user"`.

## 4. Python HTTP server and provider calls

Start the server with the command described in
[README.md:91-95](README.md#L91-L95). The server exposes:

- `GET /api/models`: returns configured models and availability
  ([mirage_demo/server.py:64-66](mirage_demo/server.py#L64-L66)).
- `POST /api/generate`: validates the request, selects the backend, and calls
  `backend.generate(messages, api_key=...)`
  ([mirage_demo/server.py:69-102](mirage_demo/server.py#L69-L102)).

If the backend call fails, the server prints the reason to its terminal and
returns HTTP 502 with a readable `error` field instead of dropping the
connection ([mirage_demo/server.py:96-102](mirage_demo/server.py#L96-L102)).

### 4.1 OpenAI-compatible providers

`OpenAICompatBackend.generate()` builds a Chat Completions payload containing
the configured model, all messages, `temperature: 0.0`, `max_tokens: 700`, and
`stream: false` ([mirage_demo/models.py:177-184](mirage_demo/models.py#L177-L184)).

It then sends:

```text
POST {base_url}/chat/completions
Content-Type: application/json
Authorization: Bearer <key>   # only when a key exists
```

The request is made at
[mirage_demo/models.py:186-187](mirage_demo/models.py#L186-L187). The response
text is read from `choices[0].message.content`
([mirage_demo/models.py:192-198](mirage_demo/models.py#L192-L198)). This one
implementation covers LM Studio, llama.cpp server, vLLM, Ollama, OpenAI, and
other compatible services; only `base_url` and `model` differ.

The web UI has an API token field. Its value is sent in the
`api_key` property of each `/api/generate` request and is used only for that
request; it is not written to `models.yaml` or persisted by the server.
The server checks it and passes it to the selected backend at
[mirage_demo/server.py:80-94](mirage_demo/server.py#L80-L94), and the
OpenAI-compatible backend sends it as `Bearer <key>` at
[mirage_demo/models.py:179-182](mirage_demo/models.py#L179-L182). Anthropic
uses the same field as its `x-api-key` value at
[mirage_demo/models.py:229-230](mirage_demo/models.py#L229-L230).

The token field is optional for local servers that do not require
authentication. It is a password input in the browser, but anyone with access
to the local page/server can still inspect or use it.

Every successful backend call, including the stub, prints the exact system
prompt, prompt, and answer to the terminal running the server/results runner.
The logger is
[mirage_demo/models.py:134-144](mirage_demo/models.py#L134-L144); it does not
print the API token.

### 4.2 Anthropic

`AnthropicBackend.generate()` separates system messages from user/assistant
conversation messages ([mirage_demo/models.py:226-228](mirage_demo/models.py#L226-L228)).
It sends the Anthropic-specific payload with `system`, `messages`, `model`,
`max_tokens`, and `temperature` ([mirage_demo/models.py:229-232](mirage_demo/models.py#L229-L232)):

```text
POST https://api.anthropic.com/v1/messages
content-type: application/json
anthropic-version: 2023-06-01
x-api-key: <key>
```

The request is at
[mirage_demo/models.py:234-235](mirage_demo/models.py#L234-L235), and text is
concatenated from response content blocks at
[mirage_demo/models.py:242](mirage_demo/models.py#L242).

### 4.3 Offline stub and fallback behavior

The `StubBackend` is not an LLM. It deterministically inspects the context it
was given and returns pre-written answers
([mirage_demo/models.py:54-108](mirage_demo/models.py#L54-L108)). It is useful
for an offline smoke test and deliberately demonstrates that the selected
world/context controls what can be answered.

In the browser, if the model call fails, `respondLive()` records the error
code and the server's message in the turn's notes and writes that reply with
the offline stub instead
([web_src/engine.js:441-446](web_src/engine.js#L441-L446)); the page shows
the note next to the reply.

In Python, if a live backend raises `BackendError`, `respond_live()` records
the error and generates the reply with the stub instead
([mirage_demo/live.py:202-209](mirage_demo/live.py#L202-L209)). The metadata
marks the backend as `"stub (fallback)"`, so this is not silent model
substitution.

## 5. Python results-runner flow

`results.py` loads the registry at
[results.py:57](results.py#L57), resolves model names at
[results.py:35-42](results.py#L35-L42), and runs each model/condition cell at
[results.py:79-94](results.py#L79-L94).

Each cell calls `run_campaign()` from
[mirage_demo/autopilot.py:210-271](mirage_demo/autopilot.py#L210-L271).
For each attacker probe, the campaign calls
`respond_live(app, session, probe, backend, ...)`
([mirage_demo/autopilot.py:227](mirage_demo/autopilot.py#L227)). The
consistency re-asks after A1–A6
([mirage_demo/autopilot.py:89-125](mirage_demo/autopilot.py#L89-L125)) and the
honeytoken replay in a fresh session
([mirage_demo/autopilot.py:251-268](mirage_demo/autopilot.py#L251-L268)) are
`respond_live()` calls too. Thus the results runner eventually reaches the
same backend `generate()` calls described above, but without the browser or
HTTP bridge.

If `--judge` names another model, the attacker's self-assessment after each
objective is also an LLM call: `_assess()` builds a transcript prompt and calls
`judge_backend.generate(...)`
([mirage_demo/autopilot.py:128-145](mirage_demo/autopilot.py#L128-L145)).
With the default `--judge regex`, no judge-model call is made; a handwritten
regex judge is used instead ([mirage_demo/autopilot.py:60-64](mirage_demo/autopilot.py#L60-L64)).
The decision to stop retrying an objective always uses the regex judge on the
last reply, whatever `--judge` is
([mirage_demo/autopilot.py:231-234](mirage_demo/autopilot.py#L231-L234)).

## 6. What is sent to the model, and what is not

For a normal model-generated turn, the effective prompt is assembled by
`_instructions()` ([mirage_demo/live.py:117-144](mirage_demo/live.py#L117-L144);
in the browser [web_src/engine.js:501-523](web_src/engine.js#L501-L523)).
It can contain:

- operator configuration/system prompt;
- session verification state;
- retrieved knowledge-base passages, with neutral `KB-…` ids in both worlds;
- CRM records;
- previously committed shadow facts;
- the refund action convention.

The selected world and these fields are assembled in
`_generate_with()` ([mirage_demo/live.py:276-361](mirage_demo/live.py#L276-L361)).
The customer probe and prior conversation history are then appended before the
backend call ([mirage_demo/live.py:339-341](mirage_demo/live.py#L339-L341)).
Turns where the shadow held back (before the concession turn) are left out of
that history ([mirage_demo/autopilot.py:274-281](mirage_demo/autopilot.py#L274-L281);
in the browser [web_src/app.js:69-73](web_src/app.js#L69-L73)).

The quarantined instance's configuration depends on the session status
([mirage_demo/live.py:289-328](mirage_demo/live.py#L289-L328);
in the browser [web_src/engine.js:553-578](web_src/engine.js#L553-L578)):

- **Not verified** (before the concession turn): the shadow system prompt and a
  `NOT VERIFIED` status telling the model to decline.
- **Verified**: a "staff mode" configuration and a `VERIFIED` status with an
  instruction for each objective. The shadow system prompt is **not** in this
  context. Asked for its configuration, the model writes `{{CONFIGURATION}}`
  and the application inserts the shadow prompt verbatim after generation
  ([mirage_demo/live.py:354-355](mirage_demo/live.py#L354-L355)). The
  consistency ledger stores the placeholder, not the document.
- A correction the verified shadow accepts (A5) is written into that session's
  shadow facts before generation, so the reply already sees it, and is undone
  if the reply declines ([mirage_demo/live.py:280-286](mirage_demo/live.py#L280-L286),
  [mirage_demo/live.py:419-424](mirage_demo/live.py#L419-L424)).

The mechanism detects leaked real assets after generation in
[_after_real()](mirage_demo/live.py#L393) and records shadow/fabricated assets
and consistency commitments in [_after_shadow()](mirage_demo/live.py#L407).
Refunds are handled separately: shadow refunds are simulated, while a real
world refund appends to `real_tool_calls` and marks a real effect
([mirage_demo/live.py:364-390](mirage_demo/live.py#L364-L390)).

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
  [README.md:38-42](README.md#L38-L42).
- To use a local server, configure its OpenAI-compatible `/v1` base URL in
  [models.yaml](models.yaml) and ensure the server is listening before starting
  `mirage_demo.server`.
- To see why a model call failed, read the server's terminal (lines starting
  with `!!! model call failed`) or the note next to the reply in the page.
- To determine whether a reply came from the requested model or the fallback,
  inspect the turn metadata: `respond_live()` explicitly sets
  `meta["backend"]` and changes it to `"stub (fallback)"` after an error
  ([mirage_demo/live.py:193](mirage_demo/live.py#L193),
  [mirage_demo/live.py:209](mirage_demo/live.py#L209)).
