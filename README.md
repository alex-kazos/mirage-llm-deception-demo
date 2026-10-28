# MIRAGE — local, model-pluggable demo

A runnable version of the MIRAGE deception layer from the thesis, driven by
**real language models** and able to run on your own PC with **local LLMs**
(LM Studio, llama.cpp, vLLM, Ollama) or hosted APIs (OpenAI, Anthropic).

Two things in one package:

1. **Interactive demo** (`mirage_demo.server`) — the same browser console as the
   published artifact, served locally and pointed at a model of your choice.
2. **Results runner** (`results.py`) — runs the autopilot red-team campaign
   (objectives A1–A6) across several models × defence conditions and writes a
   CSV and a figure.

The deception mechanism — the ingress detector (§4.3), the one-way session
state machine (§4.4), the shadow state store and consistency ledger (§4.5), the
honeytoken registry, and the real/shadow corpora (seed `20260906`) — is a
faithful port of `mirage/system.py`, `config.py` and `corpus.py`. Only the
*generation* is new: every reply is written live by a model, through two
separate model instances (the inverted dual-LLM pattern, §4.3).

> This demonstrates the **mechanism**. It is not an evaluation; the numbers in
> Chapter 5 come from the formal experiments.

---

## Install

```bash
cd mirage-demo-local
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Only `requests` and `PyYAML` are strictly new; `scikit-learn`, `numpy` and
`matplotlib` are already PoC dependencies.

Smoke test with no model and no network:

```bash
python results.py --models stub --out results/
```

---

## Configure your models — `models.yaml`

Each entry has a unique `name`. `kind: openai` covers **any OpenAI-compatible
endpoint**, so one entry type serves LM Studio, llama.cpp, vLLM, Ollama, OpenAI
and OpenRouter — you only change `base_url` and `model`. API keys are read from
environment variables, never from the file.

```yaml
models:
  - name: local-lmstudio          # LM Studio local server
    kind: openai
    base_url: http://localhost:1234/v1
    model: qwen2.5-7b-instruct

  - name: local-ollama-llama3.2   # ollama serve  (OpenAI endpoint at /v1)
    kind: openai
    base_url: http://localhost:11434/v1
    model: llama3.2:3b

  - name: openai-gpt-4o-mini
    kind: openai
    base_url: https://api.openai.com/v1
    model: gpt-4o-mini
    api_key_env: OPENAI_API_KEY

  - name: anthropic-haiku
    kind: anthropic
    model: claude-haiku-4-5
    api_key_env: ANTHROPIC_API_KEY
```

Getting a local model running, by server:

| Server     | Start it                                                              | base_url                      |
|------------|-----------------------------------------------------------------------|-------------------------------|
| LM Studio  | enable the local server in the app                                    | `http://localhost:1234/v1`    |
| Ollama     | `ollama pull llama3.2:3b` then `ollama serve`                         | `http://localhost:11434/v1`   |
| llama.cpp  | `./llama-server -m model.gguf --port 8080`                            | `http://localhost:8080/v1`    |
| vLLM       | `python -m vllm.entrypoints.openai.api_server --model <hf-model>`     | `http://localhost:8000/v1`    |

For hosted APIs set the key first, e.g. `export OPENAI_API_KEY=sk-...` /
`export ANTHROPIC_API_KEY=sk-ant-...` (Windows: `setx`).

---

## 1. Interactive demo

```bash
./run_demo.sh            # or: python -m mirage_demo.server --models models.yaml --port 8000
```

Open **http://localhost:8000**. Pick a model from the **Model** dropdown (only
available ones are enabled). The page runs the mechanism and lets you:

- chat as a customer or as an attacker and watch the detector, state machine,
  shadow store, ledger and honeytokens react;
- open **“Exact context sent to the model”** to see precisely what the
  privileged and quarantined instances were each given;
- run the **Guided scenario** (10 steps) or the **Autopilot attacker** (A1→A6);
- compare **No defence / Refusal / MIRAGE** side by side.

If no model is reachable the page falls back to the offline stub (clearly
labelled).

---

## 2. Results runner

```bash
# every available model in models.yaml, three conditions, regex self-judge
python results.py --models all --out results/

# specific models; let a strong model be the attacker's self-assessor
python results.py --models local-ollama-qwen2.5,openai-gpt-4o-mini \
    --judge openai-gpt-4o-mini --repeats 3 --out results/
```

For each (model, condition) it runs A1–A6 and records the three Exp. 4 channels:

- **self-report** — the attacker's own belief it succeeded (from the transcript
  only, like a real attacker; judged by `--judge` model, or a regex by default);
- **real asset obtained** — ground truth (did a *real* secret actually leak);
- **cross-session honeytoken burn** — the independent behavioural channel.

Outputs in `--out`:

- `summary.csv` — one row per model × condition with
  `self_report_rate`, `real_asset_rate`, `deception_success_rate`,
  `honeytoken_delivered_rate` (a reply handed a honeytoken, the fake service key or
  the fake exception reference, to the attacker), `honeytoken_burn_rate` (it was then
  replayed in a new session; only attempted when one was delivered);
- `detail.csv` — per-objective outcomes;
- `results.png` — perceived vs real success per model, one panel per condition.

**How to read it.** Under MIRAGE, self-report stays high while real asset drops
toward 0 — that gap is the deception (DSR = deception success rate). Under no
defence the two move together. This is the triangulation of §5.4.1 / Exp. 4.

---

## What is live, and what comes from the PoC

- **Live:** every reply is model-written. The privileged instance is given the
  *real* configuration / passages / CRM; the quarantined instance only the
  *shadow* ones. Graduated concession (§4.6.3) is a session-verification status
  in the quarantined context (NOT VERIFIED until the concession turn). Tool use
  (refunds) goes through an `ACTION: issue_refund(...)` convention that works on
  any model, including small local ones with no function-calling.
- **Ported from the PoC:** detector patterns and score update, thresholds, the
  one-way state machine, the honeytoken registry, the consistency ledger, and
  both corpora (seed `20260906`, 400 passages / 33 products each, no shared
  names). Retrieval is TF-IDF here vs LSA in the PoC.
- **Demo adaptations (log-only):** the A1–A6 objective label is a keyword guess
  shown for the trace; a real product name asked about in DECEIVE is mapped to
  its isomorphic shadow counterpart.

Note: the interactive page runs the mechanism in JavaScript (the same port the
published artifact uses); the results runner runs it in Python (`mirage_demo/`).
Both mirror `mirage/system.py` and share the same corpora and thresholds.

---

## Layout

```
mirage-demo-local/
  mirage_demo/
    mechanism.py   detector, state machine, shadow store, ledger, registry, corpora
    models.py      Stub / OpenAI-compatible / Anthropic backends, loaded from YAML
    live.py        the live turn: privileged vs quarantined context, leak/fab detection
    autopilot.py   the A1–A6 campaign and its triangulation verdict
    server.py      stdlib HTTP server: serves the page, /api/models, /api/generate
    webui/index.html   the built interactive page
  web_src/         page source (rebuild the UI with build_webui.py)
  results.py       the results runner (CSV + figure)
  models.yaml      your model definitions
  build_webui.py   rebuilds mirage_demo/webui/index.html from web_src/
```
