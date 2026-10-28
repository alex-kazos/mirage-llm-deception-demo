#!/usr/bin/env python3
"""Build mirage_demo/webui/index.html for the local server.

Reuses the validated demo page (the same mechanism port the published artifact
uses) and swaps its model layer: instead of the claude.ai `sample` capability,
`window.claude.use('sample')` is shimmed to POST to this project's /api/generate,
and a model dropdown (populated from /api/models) chooses the backend. Copy that
names "Claude" is made model-neutral, since the model may be a local LLM.

Point SRC at your artifact assets (the folder with demo.src.html, engine.js,
app.js, corpora.json). Defaults to ../mirage-demo next to this file.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "web_src"
OUT = ROOT / "mirage_demo" / "webui" / "index.html"

src = (SRC / "demo.src.html").read_text(encoding="utf-8")
engine = (SRC / "engine.js").read_text(encoding="utf-8")
app = (SRC / "app.js").read_text(encoding="utf-8")
corpora = (SRC / "corpora.json").read_text(encoding="utf-8")

# 1) model dropdown in the header, before the generator toggle
src = src.replace(
    '      <div class="seg" role="group" aria-label="Who writes the replies">\n'
    '        <button type="button" id="gen-claude" aria-pressed="true">Claude</button>\n'
    '        <button type="button" id="gen-stub" aria-pressed="false">Offline stub</button>\n'
    '      </div>\n'
    '      <span class="status" id="llm-status" role="status">Connecting to Claude…</span>',
    '      <label class="field" for="model">Model\n'
    '        <select id="model"><option value="stub">offline stub</option></select>\n'
    '      </label>\n'
    '      <label class="field" for="api-token">API token\n'
    '        <input id="api-token" type="password" autocomplete="off" placeholder="optional">\n'
    '      </label>\n'
    '      <div class="seg" role="group" aria-label="Who writes the replies">\n'
    '        <button type="button" id="gen-claude" aria-pressed="true">Model</button>\n'
    '        <button type="button" id="gen-stub" aria-pressed="false">Offline stub</button>\n'
    '      </div>\n'
    '      <span class="status" id="llm-status" role="status">Connecting to model server…</span>')

# 2) make the title/subtitle say this is the local build
src = src.replace("<title>MIRAGE Demo Console</title>", "<title>MIRAGE Local Console</title>")
src = src.replace("Helios Support, defended by a deception layer",
                  "Helios Support, defended by a deception layer · local models")

# 3) model-neutral copy in app.js (the model may be a local LLM, not Claude)
repl = [
    ('"connecting", sample: null, tools: false, ready: null, reason: ""',
     '"connecting", sample: null, tools: false, ready: null, reason: ""'),  # no-op anchor
    ('Connecting to Claude…', 'Connecting to model server…'),
    ('Claude connected · ${tierName}', '${(window.__mirageModel && window.__mirageModel()) || S.llm.modelName || "model"} · local server'),
    ('Claude available · stub selected', 'Model server available · stub selected'),
    ('Claude unavailable', 'Model server unavailable'),
    ('Claude is not reachable from this copy of the page (opened as a local file, or outside the Claude app).',
     'The model server did not answer. Start it with: python -m mirage_demo.server'),
    ('Claude was not allowed for this page, so replies now come from the offline stub. Reload the page to be asked again.',
     'The model server refused the request, so replies now come from the offline stub.'),
    ('Claude is unavailable here (${r.meta.llmError}), so replies now come from the offline stub.',
     'The model server is unavailable (${r.meta.llmError}), so replies now come from the offline stub.'),
    ('The offline stub returns the PoC\'s deterministic, pre-written replies. Switch to Claude for model-generated answers.',
     'The offline stub returns deterministic, pre-written replies. Switch to a model for generated answers.'),
    ('Claude writes every reply; the first message asks you to allow it.', 'The selected model writes every reply.'),
    ('privileged">Claude · privileged instance', 'privileged">Model · privileged instance'),
    ('shadow">Claude · quarantined instance', 'shadow">Model · quarantined instance'),
    ('plain">Claude · refusal writer', 'plain">Model · refusal writer'),
    ('judged by Claude', 'judged by the model'),
    ('done[0].by === "claude" ? "Claude" : "a regex"', 'done[0].by === "regex" ? "a regex" : "the model"'),
    ('Every reply is written live by Claude.', 'Every reply is written live by the selected model.'),
    ('<label for="cfg-tier">Claude model <select id="cfg-tier"><option value="quick"${S.tier === "quick" ? " selected" : ""}>Fast (answers in seconds)</option><option value="default"${S.tier === "default" ? " selected" : ""}>Standard (thinks first, slower)</option></select></label>',
     '<label>Model <span class="muted">chosen in the top bar</span></label>'),
    ('panel.querySelector("#cfg-tier").addEventListener("change", (e) => { S.tier = e.target.value; renderHeader(); });',
     'const _t = panel.querySelector("#cfg-tier"); if (_t) _t.addEventListener("change", (e) => { S.tier = e.target.value; renderHeader(); });'),
]
for a, b in repl:
    app = app.replace(a, b)

# carry the selected model name into the status line
app = app.replace('S.llm.sample = sample;', 'S.llm.sample = sample; S.llm.modelName = (window.__mirageModel && window.__mirageModel()) || "model";')

# 4) the shim: window.claude.use('sample') -> POST /api/generate; populate #model
shim = r"""
<script>
/* Local model bridge. Replaces the claude.ai `sample` capability with a call to
   this project's own server, so the same page runs against any configured model. */
(function () {
  var sel = document.getElementById('model');
  var token = document.getElementById('api-token');
  window.__mirageModel = function () { return sel ? sel.value : 'stub'; };
  fetch('/api/models').then(function (r) { return r.json(); }).then(function (d) {
    if (!sel || !d.models || !d.models.length) return;
    sel.innerHTML = d.models.map(function (m) {
      return '<option value="' + m.name + '">' +
        m.name + (m.available ? '' : ' (' + m.reason + ')') + '</option>';
    }).join('') + '<option value="stub">offline stub</option>';
    var first = d.models.filter(function (m) { return m.available; })[0];
    if (first) sel.value = first.name;
  }).catch(function () {});

  async function callServer(input) {
    var messages = typeof input === 'string' ? [{ role: 'user', content: input }] : input;
    var r = await fetch('/api/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: window.__mirageModel(),
        api_key: token ? token.value : '',
        messages: messages,
      }),
    });
    var d = await r.json();
    if (!r.ok) { var e = new Error(d.error || 'server error'); e.code = 'upstream_error'; throw e; }
    return d.text || '';
  }

  window.claude = {
    use: async function (name) {
      if (name !== 'sample') return null;
      var sample = async function (input, opts) {
        opts = opts || {};
        var text = await callServer(input);
        if (opts.onText) opts.onText({ text: text, delta: text });
        return { text: text, truncated: false, modelTierApplied: 'local' };
      };
      sample.json = async function (input, opts) {
        var text = await callServer(input);
        var t = text.trim();
        var fence = t.match(/```(?:json)?\s*([\s\S]*?)```/);
        if (fence) t = fence[1].trim();
        try { return JSON.parse(t); } catch (e) {}
        var i = t.indexOf('{'), j = t.lastIndexOf('}');
        if (i >= 0 && j > i) { try { return JSON.parse(t.slice(i, j + 1)); } catch (e) {} }
        var err = new Error('invalid json'); err.code = 'invalid_json'; err.text = text; throw err;
      };
      sample.limits = async function () { return { maxPromptBytes: 262144 }; };
      return sample;
    },
  };
})();
</script>
"""

out = (src
       .replace('<script type="application/json" id="corpora">__CORPORA__</script>',
                shim + '\n<script type="application/json" id="corpora">' + corpora + '</script>')
       .replace('/*__ENGINE__*/', engine)
       .replace('/*__APP__*/', app))

# visible phrases that survive in engine.js / GEN_TEXT (bare "Claude" also names
# JS identifiers, so only replace unambiguous phrases)
for a, b in [
    ("Claude · privileged instance", "Model · privileged instance"),
    ("Claude · quarantined instance", "Model · quarantined instance"),
    ("Claude · refusal writer", "Model · refusal writer"),
    ("context sent to Claude", "context sent to the model"),
    ("Claude call failed", "Model call failed"),
]:
    out = out.replace(a, b)

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(out, encoding="utf-8")
print(f"wrote {OUT}  ({len(out)} bytes)")
