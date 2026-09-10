"""Shared HTML for the two Flask front ends in this repo.

``bridge_ui.py`` (the whole bridge UI, port 5000) and ``decoder_standalone.py``
(the same decoder wizard on its own, port 5001) render the same wizard markup.
Until this module existed they held two byte-identical copies of it -- 108 of
``decoder_standalone.py``'s 179 lines were a duplicate of ``bridge_ui.py`` -- so
an edit to one page silently diverged from the other. The shared markup lives
here now and both pages import it.

Standard library only, deliberately: no Flask, no ``template``. Nothing here is
a Jinja template either. The constants are concatenated by the importing module,
never ``%``-formatted or f-stringed, so the ``{{ ... }}`` Jinja tags and the
``${ ... }`` JavaScript template literals the pages contain need no escaping.

Every constant below is a block of whole lines and ends with a newline, so a
page is assembled by concatenation with its own chunks in between.
"""

# Everything above the per-page <title>: doctype, <html>, <head> and the two
# meta tags. The page supplies its own <title> line, then HEAD_END.
HEAD_START = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
"""

# Stylesheet link and the close of <head> / open of <body>.
HEAD_END = r"""  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
"""

# The decoder wizard itself: Packet View, Byte Diff, Field Definitions, Test
# Decode Results and Generate Template. Self-contained -- every element opened
# here is closed here -- so a page can drop it inside whatever wrapper it uses
# (bridge_ui.py puts it in a .tab-content, decoder_standalone.py in a plain
# padded <div>). The ids and classes are the contract with static/app.js.
WIZARD_PANELS = r"""  <!-- Packet Header -->
  <div class="wizard-panel">
    <h3>Packet View</h3>
    <div style="display:flex; align-items:center; gap:12px; margin-bottom:8px;">
      <button class="btn small secondary" onclick="prevPacket()">◀ Prev</button>
      <span id="packet-counter">No packets loaded</span>
      <button class="btn small secondary" onclick="nextPacket()">Next ▶</button>
    </div>
    <div id="packet-view"></div>
  </div>

  <!-- Byte Diff -->
  <div class="wizard-panel">
    <h3>Byte Diff (changes highlighted)</h3>
    <div id="diff-view"></div>
  </div>

  <!-- Field Definitions -->
  <div class="wizard-panel">
    <h3>Field Definitions</h3>
    <p style="color: var(--text2); margin-bottom:8px; font-size:0.85rem;">
      Click a field row to select it, then click bytes in the hex view to set start/end positions.
    </p>
    <table class="field-table">
      <thead>
        <tr>
          <th>Name</th><th>Start</th><th>End</th><th>Type</th>
          <th>Scale</th><th>Unit</th><th>Preview</th><th></th>
        </tr>
      </thead>
      <tbody id="field-table-body"></tbody>
    </table>
    <div style="margin-top:12px; display:flex; gap:8px;">
      <button class="btn secondary" onclick="addField()">+ Add Field</button>
      <button class="btn secondary" onclick="testDecode()">Test Decode</button>
    </div>
  </div>

  <!-- Test Results -->
  <div class="wizard-panel">
    <h3>Test Decode Results</h3>
    <div id="test-results" style="overflow-x:auto;"></div>
  </div>

  <!-- Generate Template -->
  <div class="card">
    <h3>Generate Template</h3>
    <div class="form-row">
      <div class="form-group">
        <label>Template ID</label>
        <input id="tpl-id" placeholder="my_device">
      </div>
      <div class="form-group">
        <label>Name</label>
        <input id="tpl-name" placeholder="My Device">
      </div>
      <div class="form-group">
        <label>Manufacturer</label>
        <input id="tpl-manufacturer" placeholder="Brand">
      </div>
      <div class="form-group">
        <label>Model</label>
        <input id="tpl-model" placeholder="Model X">
      </div>
    </div>
    <div class="form-row">
      <div class="form-group">
        <label>Characteristic UUID</label>
        <input id="tpl-characteristic" placeholder="0000xxxx-0000-1000-8000-00805f9b34fb">
      </div>
      <div class="form-group">
        <label>&nbsp;</label>
        <button class="btn" onclick="generateTemplate()">Download Template JSON</button>
      </div>
    </div>
  </div>
"""

# Toast mount point, the shared app.js, and the opening tag of the per-page
# inline script. The page writes its own script body, then PAGE_END.
SCRIPTS_START = r"""<div class="toast-container"></div>

<script src="/static/app.js"></script>
<script>
"""

# Close of the inline script and of the document.
PAGE_END = r"""</script>

</body>
</html>
"""
