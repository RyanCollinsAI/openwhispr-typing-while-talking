"""Build the patched renderer bundle for whisper-window-streaming.

The main-process half of that patch (ipcHandlers.js) does nothing on its own,
because all three of its renderer-side preconditions live in the minified Vite
bundle:

  1. the preview worklet only starts when `showTranscriptionPreview` is on
     (default false) or the provider is NVIDIA - so on whisper no PCM was
     ever streamed to the main process at all;
  2. `start-dictation-preview` is called without `commit`, and the handler
     reads `dictationCommitEnabled = !!commit && ...`, so it stayed false;
  3. `processWithLocalWhisper` ignores `options.streamedText` entirely - only
     the Parakeet path honours it - so committed text would be thrown away
     and the whole clip decoded anyway.

This adds the three missing pieces. `display` is left alone, so no preview
window appears; the session runs headless purely to feed the commit path,
which `start-dictation-preview` explicitly supports.

Source is the LIVE bundle (already carrying the byte patches), because
apply_patches.py writes byte patches first and then overwrites whole files - so
a replacement file has to carry the byte patches with it. `base_sha256` still
points at the stock bundle.

Needs OpenWhispr 1.7.6 installed with the byte patches applied, and a copy of the
stock app file named app.asar.bak-1.7.6 next to app.asar.

    py make_renderer_patch.py            build files-1.7.6/... and print hashes
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from asar_tool import Asar  # noqa: E402

HERE = Path(__file__).parent
LOCAL_APPDATA = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
LIVE = LOCAL_APPDATA / "Programs" / "OpenWhispr" / "resources" / "app.asar"
STOCK = LIVE.with_name("app.asar.bak-1.7.6")
TARGET = "src/dist/assets/index-Bj5ExvhP.js"
OUT = HERE / "files-1.7.6" / TARGET

# (what to find, what to write, why)
EDITS = [
    (
        # 1. Open the gate for local whisper. `ow_c` means "this recording wants
        #    windowed commit": local provider, not NVIDIA (which has its own
        #    online stream in `u`).
        "l=o===`nvidia`,u=a&&l&&se(c);if(this._streamingCommitActive=!1,a&&(i||u))try{",
        "l=o===`nvidia`,u=a&&l&&se(c),ow_c=a&&!l;"
        "if(this._streamingCommitActive=!1,a&&(i||u||ow_c))try{",
        "start the preview worklet for local whisper",
    ),
    (
        # 2. Actually ask for the commit path, and remember to collect its
        #    result at stop. display is untouched, so nothing is shown.
        #
        #    initialPrompt matters as much as commit does. The handler reads the
        #    custom-dictionary prompt off this call, and the renderer did not
        #    send it, so every window would have decoded with no dictionary at
        #    all.
        "startDictationPreview?.({provider:e,model:t,language:r,display:i}),"
        "this._streamingCommitActive=u",
        "startDictationPreview?.({provider:e,model:t,language:r,display:i,commit:ow_c,"
        "initialPrompt:this.getCustomDictionaryPrompt()}),"
        "this._streamingCommitActive=u||ow_c",
        "pass commit:true, the dictionary prompt, and await the committed text",
    ),
    (
        # 3a. Capture streamedText before the inner `let n` shadows the options.
        "async processWithLocalWhisper(e,t=`base`,n={}){let r={};try{"
        "let n=await e.arrayBuffer(),",
        "async processWithLocalWhisper(e,t=`base`,n={}){"
        "let r={},ow_s=typeof n?.streamedText==`string`?n.streamedText.trim():``;try{"
        "let n=ow_s?null:await e.arrayBuffer(),",
        "read options.streamedText on the whisper path",
    ),
    (
        # 3b. Use it instead of decoding the whole recording again.
        "let s=performance.now(),c=await window.electronAPI.transcribeLocalWhisper(n,a);",
        "let s=performance.now(),"
        "c=ow_s?{success:!0,text:ow_s}:await window.electronAPI.transcribeLocalWhisper(n,a);",
        "skip the whole-clip decode when the windows already produced the text",
    ),
]


def main() -> int:
    if sys.platform != "win32":
        print("ERROR: Windows only. This works on the Windows build of OpenWhispr 1.7.6.")
        return 5
    for label, p in (("OpenWhispr's app file", LIVE), ("the stock app file copy", STOCK)):
        if not p.is_file():
            print(f"ERROR: {label} was not found: {p}")
            if p is STOCK:
                print("Copy app.asar to app.asar.bak-1.7.6 (from a fresh 1.7.6 install) first.")
            else:
                print("Install OpenWhispr 1.7.6 first.")
            return 5
    live = Asar.load(LIVE).read(TARGET).decode("utf-8")
    stock = Asar.load(STOCK).read(TARGET).decode("utf-8")

    out = live
    for find, repl, why in EDITS:
        n = out.count(find)
        if n != 1:
            print(f"REFUSING: anchor appears {n} times ({why}):\n  {find[:90]}")
            return 1
        out = out.replace(find, repl)
        print(f"  ok  {why}")

    data = out.encode("utf-8")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(data)

    print()
    print(f"  path            {TARGET}")
    print(f"  base_sha256     {hashlib.sha256(stock.encode('utf-8')).hexdigest()}")
    print(f"  base_size       {len(stock.encode('utf-8'))}")
    print(f"  patched_sha256  {hashlib.sha256(data).hexdigest()}")
    print(f"  patched_size    {len(data)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
