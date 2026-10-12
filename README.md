# OpenWhispr: typing while you talk (unofficial patch)

**This is an unofficial patch for OpenWhispr. It is not part of OpenWhispr, and the OpenWhispr team does not support it.**

## What it supports

| | Supported |
|---|---|
| Operating system | **Windows only** |
| Windows version it was checked on | Windows 11 Pro, version 10.0.26200, 64-bit |
| OpenWhispr version | **1.7.6 only** |

**It does not work on macOS or Linux.**
The patch changes exact bytes and whole files inside the Windows build of OpenWhispr 1.7.6.
That cannot run on other systems, and the scripts stop with a message if you try.

**The latest OpenWhispr release (1.10.2) is not supported yet.**
Run against another version, the patcher skips every patch it cannot match and logs a warning.

This repo is a private draft.

## What it does

Normally OpenWhispr waits until you stop talking, then turns the whole recording into text.
This patch turns long recordings into text in pieces while you are still talking.
When you stop, only the last small piece is left to do.

Measured on one Windows desktop with an Nvidia card and the large-v3 model:

| Clip length | Wait after you stop, before | Wait after you stop, now |
|---|---|---|
| 34 to 50 seconds | 2.2 seconds (median) | 0.9 seconds (median) |

On a CPU-only laptop with the base model the wait is longer.
One 33 second clip took 2.7 seconds to finish after stop.
Pieces are 12 seconds long, so recordings under 12 seconds see no change.

## Before you start

- Windows 64-bit.
- OpenWhispr **1.7.6**, installed for your user (the default).
  Not 1.10.2.
- Python 3 (the `py` launcher). The scripts were written and run with Python 3.14.
- `apply_patches.py` needs only the Python standard library.
  `verify_streaming.py` and `owcdp.py` also need `websocket-client`:
  `py -m pip install websocket-client`
- Never launch OpenWhispr with a test flag such as `--use-fake-device-for-media-stream`.
  It can replace your microphone, and every dictation will transcribe the test file.

## Setup steps

1. Install OpenWhispr 1.7.6 from the release page:
   https://github.com/OpenWhispr/openwhispr/releases/tag/v1.7.6
   The silent install command is `OpenWhispr-Setup-1.7.6.exe /S`.
   It installs for your user into `%LOCALAPPDATA%\Programs\OpenWhispr`.
2. Open the app once. Set it to use local Whisper and pick a model for your computer.
3. See what would change, without changing anything:
   `py apply_patches.py --check`
4. Apply the patch:
   `py apply_patches.py`
   The patcher stops OpenWhispr if it is running, saves a backup of the app file next to it, applies the patches, and starts OpenWhispr again.
5. Prove it works.
   First close your normal OpenWhispr copy (right-click its tray icon, then Quit).
   This test opens a visible app window and registers the same hotkey, so two copies fight.
   Start OpenWhispr with the debug port open:
   `"%LOCALAPPDATA%\Programs\OpenWhispr\OpenWhispr.exe" --remote-debugging-port=9222`
   Then run it with a speech recording **longer than 31 seconds** (a 16 kHz mono 16-bit `.wav`):
   `py verify_streaming.py --wav <your-clip.wav>`
   The model must be downloaded first (step 2), or pass one you have with `--model <name>`.
   If it is missing, the script says `MODEL NOT DOWNLOADED` and stops. That is not a patch failure.
   `PASS` means the piece-by-piece path really ran.
   `--check` alone only proves the bytes are there, not that the feature works.
   Close that copy of OpenWhispr afterwards and start it normally.
   The debug port lets any program on your computer control the app.
6. Undo it any time:
   `py apply_patches.py --rollback`
   This restores the newest backup of the app file and restarts OpenWhispr.

If a file is missing, each script prints a message that names the file and what to do.

Known limit: OpenWhispr reads Whisper models from your real Windows profile folder (`%USERPROFILE%\.cache\openwhispr\whisper-models`).
Changing the `USERPROFILE` or `HOME` environment variable for a test does not move it.
A test copy shares your downloaded models.

## What else this patch set changes

`apply_patches.py` applies every patch in this repo, not only typing while talking:

- Typing while talking (`whisper-window-streaming` and `whisper-window-streaming-renderer`).
- A small pill-shaped orb at the bottom of the screen that follows your voice.
- A tray icon that shows listening, working, and error.
- Tighter Whisper decode limits that reduce invented words such as "Thank you." on silence.
- Automatic update checks turned off, so an update cannot silently remove the patch.

Typing while talking cannot be applied on its own.

## Why only 1.7.6

OpenWhispr keeps its code in one file, `app.asar`.
The patches replace exact bytes and whole files inside it.
A new version changes those bytes, so the patch data has to be rebuilt for each new version.
That has not been done for 1.10.2.

## Files

| File | What it is |
|---|---|
| `apply_patches.py` | Applies, checks, and rolls back the patches |
| `asar_tool.py` | Reads and repacks the app file |
| `patches-1.7.6.json` | Byte patches |
| `file-patches-1.7.6.json` | Whole-file patches and their hashes |
| `files-1.7.6/` | The replacement source files |
| `verify_streaming.py` | Proves typing while talking actually runs |
| `refresh_manifest.py` | Recomputes the hashes in `file-patches-1.7.6.json` after you edit `files-1.7.6/` |
| `make_renderer_patch.py` | Rebuilds the patched app bundle from an installed copy |
| `owcdp.py` | Helper to talk to a running app over the debug port |
| `LICENSE-OpenWhispr.txt` | OpenWhispr's MIT license |

`.gitattributes` keeps every file byte for byte, because the patch checks file hashes.
Do not let your Git settings change line endings in this repo.

## License

`files-1.7.6/` contains modified copies of OpenWhispr source files.
OpenWhispr is MIT licensed. Its license is in `LICENSE-OpenWhispr.txt`.
