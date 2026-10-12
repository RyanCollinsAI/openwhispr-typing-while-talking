"""Prove the windowed-commit path actually RUNS, not just that its bytes are present.

apply_patches.py --check only asks whether the patched files are in the archive.
That is not the same question as whether the feature does anything. If the
renderer never asks for the windowed-commit path, the patch is installed but
dead, and every dictation quietly takes the old whole-clip path.

This drives the real main-process commit path on the running app over the
Chromium remote debugging port: starts a headless preview session with
commit:true, streams real PCM at it, and asks what comes back. `streamed:true`
with text means the feature is alive. `streamed:false` means the patch is
installed and dead.

It never touches the microphone, never pastes, and writes nothing to the
transcript database - it talks to the IPC handlers directly.

OpenWhispr must be running with the debug port open, for example:

    OpenWhispr.exe --remote-debugging-port=9222

Close that copy afterwards: the debug port lets any local program drive the app.

    py verify_streaming.py --wav path\to.wav     use a specific clip (recommended)
    py verify_streaming.py                       use the newest saved recording
    py verify_streaming.py --port 9446           use a different debug port

Needs a recording longer than one 30s window, or there is nothing to commit and
the honest answer is "inconclusive".
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
from pathlib import Path

try:
    import websocket
except ImportError:
    raise SystemExit("ERROR: the 'websocket-client' package is missing. "
                     "Install it with:  py -m pip install websocket-client")

LOCAL_APPDATA = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
ROAMING_APPDATA = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
APP_RESOURCES = LOCAL_APPDATA / "Programs" / "OpenWhispr" / "resources"
APPDATA = ROAMING_APPDATA / "open-whispr"
AUDIO = APPDATA / "audio"
FFMPEG = APP_RESOURCES / "app.asar.unpacked" / "node_modules" / "ffmpeg-static" / "ffmpeg.exe"
WINDOW_SECONDS = 30
CHUNK_MS = 100
ASAR = APP_RESOURCES / "app.asar"
RENDERER = "src/dist/assets/index-Bj5ExvhP.js"

# The main-process handler takes commit:true from whoever calls it, so driving
# it directly proves the decode works but says nothing about whether the app
# ever asks for it. So check the renderer's side of the contract too, by
# reading the bundle that actually ships.
RENDERER_MARKERS = {
    "commit:ow_c": "renderer asks for the commit path",
    "ow_c=a&&!l": "commit is enabled for local whisper",
    "initialPrompt:this.getCustomDictionaryPrompt()": "windows get the dictionary",
    "ow_s?{success:!0": "the committed text is used instead of a full decode",
}


class Renderer:
    """The orb window, which is where electronAPI is exposed."""

    def __init__(self, port: int):
        try:
            listing = json.load(urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/list", timeout=10))
        except OSError as e:
            raise SystemExit(
                f"ERROR: cannot reach OpenWhispr's debug port {port} ({e}).\n"
                f"Start OpenWhispr with --remote-debugging-port={port} and try again.")
        pages = [t for t in listing if t.get("type") == "page"]
        for t in pages:
            ws = websocket.create_connection(t["webSocketDebuggerUrl"],
                                             timeout=300, suppress_origin=True)
            self.ws, self._id = ws, 0
            if self.ev("typeof window.electronAPI?.startDictationPreview") == "function":
                return
            ws.close()
        raise SystemExit(f"no OpenWhispr renderer with electronAPI on port {port}")

    def ev(self, expr, await_promise=False):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({
            "id": mid, "method": "Runtime.evaluate",
            "params": {"expression": expr, "returnByValue": True,
                       "awaitPromise": await_promise}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                r = msg.get("result", {})
                if r.get("exceptionDetails"):
                    raise RuntimeError(r["exceptionDetails"].get("text"))
                return r.get("result", {}).get("value")


def newest_recording() -> Path | None:
    """The most recent recording long enough to fill a window."""
    if not AUDIO.is_dir():
        return None
    for f in sorted(AUDIO.glob("*.webm"), key=lambda p: p.stat().st_mtime,
                    reverse=True):
        # ~16 kB/s at the app's bitrate; cheap prefilter before paying ffmpeg.
        if f.stat().st_size > WINDOW_SECONDS * 12000:
            return f
    return None


def to_pcm(src: Path) -> bytes:
    """16 kHz mono s16le samples, the only format the commit path accepts."""
    if src.suffix.lower() == ".wav":
        with wave.open(str(src)) as w:
            if (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2):
                return w.readframes(w.getnframes())
    if not FFMPEG.is_file():
        raise SystemExit(
            f"ERROR: ffmpeg was not found at {FFMPEG}.\n"
            "Pass a 16 kHz mono 16-bit .wav file with --wav instead, "
            "or reinstall OpenWhispr 1.7.6.")
    tmp = Path(tempfile.gettempdir()) / f"ow-verify-{src.stem}.wav"
    subprocess.run(
        [str(FFMPEG), "-i", str(src), "-ar", "16000", "-ac", "1",
         "-c:a", "pcm_s16le", "-y", str(tmp)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    with wave.open(str(tmp)) as w:
        pcm = w.readframes(w.getnframes())
    tmp.unlink(missing_ok=True)
    return pcm


def check_renderer(asar_path: Path) -> bool:
    """Is the shipped bundle actually wired to ask for windowed commit?"""
    sys.path.insert(0, str(Path(__file__).parent))
    from asar_tool import Asar

    print("renderer wiring:")
    try:
        src = Asar.load(asar_path).read(RENDERER).decode("utf-8")
    except Exception as e:
        print(f"  cannot read {RENDERER} from {asar_path.name}: {e}")
        return False
    ok = True
    for marker, what in RENDERER_MARKERS.items():
        found = marker in src
        ok &= found
        print(f"  [{'x' if found else ' '}] {what}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--wav")
    ap.add_argument("--model", default=None)
    ap.add_argument("--asar", type=Path, default=ASAR)
    args = ap.parse_args()

    if sys.platform != "win32":
        print("ERROR: Windows only. This checks the Windows build of OpenWhispr 1.7.6.")
        return 5
    if not args.asar.is_file():
        print(f"ERROR: OpenWhispr's app file was not found: {args.asar}\n"
              "Install OpenWhispr 1.7.6 first, or pass the path with --asar.")
        return 5
    if args.wav and not Path(args.wav).is_file():
        print(f"ERROR: the recording was not found: {args.wav}")
        return 5

    wired = check_renderer(args.asar)
    print()

    src = Path(args.wav) if args.wav else newest_recording()
    if not src or not src.exists():
        print("INCONCLUSIVE: no recording longer than one 30s window to test with. "
              "Pass one with --wav <file>.")
        return 2
    pcm = to_pcm(src)
    seconds = len(pcm) / (16000 * 2)
    if seconds < WINDOW_SECONDS + 1:
        print(f"INCONCLUSIVE: {src.name} is {seconds:.1f}s, "
              f"under one {WINDOW_SECONDS}s window")
        return 2

    model = args.model
    r = Renderer(args.port)
    if not model:
        model = r.ev("localStorage.getItem('whisperModel')") or "large"
    print(f"clip {src.name}  {seconds:.1f}s  model={model}  port={args.port}")

    listing = r.ev("window.electronAPI.listWhisperModels()", await_promise=True) or {}
    status = {m.get("model"): m.get("downloaded") for m in listing.get("models", [])}
    if model in status and not status[model]:
        have = sorted(k for k, v in status.items() if v)
        print(f"MODEL NOT DOWNLOADED: '{model}' is not in {listing.get('cache_dir')}")
        print("This is not a patch failure. Download the model in OpenWhispr's "
              "settings, or pick one you have with --model "
              f"({', '.join(have) if have else 'none downloaded yet'}).")
        return 3

    started = r.ev(
        "window.electronAPI.startDictationPreview({"
        "provider:'whisper',model:%s,language:'en',display:false,commit:true,"
        "initialPrompt:null})" % json.dumps(model), await_promise=True)
    if not (started or {}).get("success"):
        print(f"FAIL: start-dictation-preview refused: {started}")
        return 1

    step = int(16000 * 2 * CHUNK_MS / 1000)
    for off in range(0, len(pcm), step):
        b64 = base64.b64encode(pcm[off:off + step]).decode()
        r.ev("(()=>{const s=atob('%s');const a=new Uint8Array(s.length);"
             "for(let i=0;i<s.length;i++)a[i]=s.charCodeAt(i);"
             "window.electronAPI.sendDictationPreviewAudio(a);return 1})()" % b64)

    t0 = time.perf_counter()
    res = r.ev("window.electronAPI.stopDictationPreview({display:false})",
               await_promise=True) or {}
    stop_ms = (time.perf_counter() - t0) * 1000

    text = (res.get("text") or "").strip()
    print("main-process commit path:")
    print(f"  streamed     {res.get('streamed')}")
    print(f"  words        {len(text.split())}")
    print(f"  drain at stop {stop_ms:.0f} ms  "
          f"(NOT the latency you feel - this harness pushes the whole clip in "
          f"at once, so every window drains after stop; in real use they decode "
          f"while you talk)")
    print(f"  text         {text[:150]}{'...' if len(text) > 150 else ''}")
    print()

    ran = bool(res.get("streamed") and text)
    if ran and wired:
        print("PASS - the windowed-commit path is wired up and it ran.")
        return 0
    if ran and not wired:
        print("FAIL - the decode path works, but the shipped renderer never asks "
              "for it, so real dictations still take the whole-clip path.")
        return 1
    print("FAIL - the patch is installed but never committed a window; "
          "dictation is silently taking the old whole-clip path.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
