"""Apply an unofficial patch set to OpenWhispr 1.7.6 (typing while you talk).

Windows only. OpenWhispr 1.7.6 only. The patches replace exact bytes and whole
files inside resources/app.asar, so they cannot work on another version or on
another operating system.

    py apply_patches.py            # patch the installed app (restarts it if it was running)
    py apply_patches.py --check    # report state, change nothing
    py apply_patches.py --asar X   # patch a specific file (testing), no app restart
    py apply_patches.py --rollback # restore the newest backup and restart

Two kinds of patch, applied in that order:

* BYTE patches (patches-<version>.json) are exact (original, patched) same-length
  pairs. They edit the archive in place, so no offset moves.
* FILE patches (file-patches-<version>.json) replace whole files inside the
  archive, which byte pairs cannot do because they may not change length. The
  replacement lives under files-<version>/ so it can be read and diffed like
  normal source. Applying one repacks the archive through asar_tool.

Either kind reports NOT FOUND and is skipped when the app is a different build
than the patch data was made for.
"""

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from asar_tool import Asar

HERE = Path(__file__).resolve().parent
LOCAL_APPDATA = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
APP_DIR = LOCAL_APPDATA / "Programs" / "OpenWhispr"
ASAR = APP_DIR / "resources" / "app.asar"
EXE = APP_DIR / "OpenWhispr.exe"
LOG = HERE / "apply.log"


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def load_patches():
    files = sorted(p for p in HERE.glob("patches-*.json"))
    if not files:
        raise SystemExit(f"ERROR: no patches-*.json found in {HERE}. "
                         "Run this script from a complete copy of the repo.")
    path = files[-1]
    pairs = json.loads(path.read_text())
    for p in pairs:
        p["orig"] = base64.b64decode(p["orig"])
        p["patched"] = base64.b64decode(p["patched"])
    return path.name, pairs


def load_file_patches():
    files = sorted(HERE.glob("file-patches-*.json"))
    if not files:
        return None, []
    path = files[-1]
    entries = json.loads(path.read_text())
    for e in entries:
        replacement = HERE / e["file"]
        if not replacement.is_file():
            raise SystemExit(f"ERROR: replacement file is missing: {replacement}. "
                             "Run this script from a complete copy of the repo.")
        e["content"] = replacement.read_bytes()
        actual = hashlib.sha256(e["content"]).hexdigest()
        if actual != e["patched_sha256"]:
            raise SystemExit(
                f"{e['file']} does not match its manifest hash - refusing to patch "
                f"with an unverified file (expected {e['patched_sha256'][:12]}, "
                f"got {actual[:12]})"
            )
    return path.name, entries


def classify(data, pairs):
    """Returns (applied, pending, missing) patch-name lists for this binary."""
    applied, pending, missing = [], [], []
    for p in pairs:
        if data.count(p["patched"]) == 1:
            applied.append(p["name"])
        elif data.count(p["orig"]) == 1:
            pending.append(p["name"])
        else:
            missing.append(p["name"])
    return applied, pending, missing


def classify_files(asar, entries):
    """Same three buckets, by hashing each target file inside the archive."""
    applied, pending, missing = [], [], []
    for e in entries:
        try:
            cur = hashlib.sha256(asar.read(e["path"])).hexdigest()
        except KeyError:
            missing.append(f"{e['name']}:{e['path']}")
            continue
        if cur == e["patched_sha256"]:
            applied.append(f"{e['name']}:{e['path']}")
        elif cur == e["base_sha256"]:
            pending.append(f"{e['name']}:{e['path']}")
        else:
            missing.append(f"{e['name']}:{e['path']}")
    return applied, pending, missing


def openwhispr_running():
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq OpenWhispr.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True,
    ).stdout
    return "OpenWhispr.exe" in out


def stop_openwhispr():
    # WM_CLOSE, not /F: a forced kill discards unflushed leveldb writes, which
    # silently reverts that session's settings on next boot.
    subprocess.run(["taskkill", "/IM", "OpenWhispr.exe", "/T"], capture_output=True)
    for _ in range(20):
        if not openwhispr_running():
            break
        time.sleep(0.5)
    else:
        subprocess.run(["taskkill", "/F", "/IM", "OpenWhispr.exe"], capture_output=True)
        for _ in range(20):
            if not openwhispr_running():
                break
            time.sleep(0.5)
    subprocess.run(["taskkill", "/F", "/IM", "whisper-server-win32-x64-cuda.exe"],
                   capture_output=True)
    time.sleep(1)


def start_openwhispr():
    # Fully detached with no inherited handles: a plain Popen from a shell left
    # the app looking healthy but saving no dictations until a clean relaunch.
    if not EXE.is_file():
        log(f"WARNING cannot restart OpenWhispr: {EXE} not found. Start it yourself.")
        return
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0))
    subprocess.Popen([str(EXE)], cwd=str(APP_DIR),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags)
    log("restarted OpenWhispr (detached)")


def backups():
    return sorted(ASAR.parent.glob(ASAR.name + ".pre-patch-*"))


def rollback():
    found = backups()
    if not found:
        log("ERROR no .pre-patch-* backup to roll back to")
        return 4
    newest = found[-1]
    was_running = openwhispr_running()
    if was_running:
        stop_openwhispr()
    ASAR.write_bytes(newest.read_bytes())
    log(f"rolled back to {newest.name}")
    if was_running:
        start_openwhispr()
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--rollback", action="store_true",
                    help="restore the newest pre-patch backup and restart")
    ap.add_argument("--asar", type=Path, default=ASAR)
    args = ap.parse_args()
    manage_app = args.asar == ASAR

    if sys.platform != "win32":
        print("ERROR: this patcher supports Windows only. It patches the Windows "
              "build of OpenWhispr 1.7.6 and cannot work on macOS or Linux.")
        return 5
    if not args.asar.is_file():
        print(f"ERROR: OpenWhispr's app file was not found: {args.asar}\n"
              "Install OpenWhispr 1.7.6 for your user first "
              "(https://github.com/OpenWhispr/openwhispr/releases/tag/v1.7.6), "
              "or pass the path with --asar.")
        return 5

    if args.rollback:
        return rollback()

    src, pairs = load_patches()
    fsrc, fentries = load_file_patches()
    data = args.asar.read_bytes()
    applied, pending, missing = classify(data, pairs)

    asar = Asar.load(args.asar)
    fapplied, fpending, fmissing = classify_files(asar, fentries)

    if missing:
        log(f"WARNING patterns not found (app updated past patch data?): {', '.join(missing)}")
    if fmissing:
        log(f"WARNING file targets changed (app updated past patch data?): {', '.join(fmissing)}")

    if not pending and not fpending:
        log(f"ok - {len(applied)}/{len(pairs)} byte patches ({src}), "
            f"{len(fapplied)}/{len(fentries)} file patches"
            f"{' (' + fsrc + ')' if fsrc else ''}, nothing to do")
        return 1 if (missing or fmissing) else 0
    if args.check:
        log(f"check - pending bytes: {', '.join(pending) or 'none'}; "
            f"pending files: {', '.join(fpending) or 'none'}")
        return 2

    log(f"applying {len(pending)} byte patch(es) and {len(fpending)} file patch(es): "
        f"{', '.join(pending + fpending)}")
    was_running = manage_app and openwhispr_running()
    if was_running:
        stop_openwhispr()
        data = args.asar.read_bytes()

    backup = args.asar.with_name(args.asar.name + f".pre-patch-{datetime.now():%Y%m%d-%H%M%S}")
    backup.write_bytes(data)

    # Byte patches first: they are in place, so offsets do not move and the
    # replacement files (which were taken from an already byte-patched archive)
    # stay consistent with them.
    for p in pairs:
        if p["name"] in pending:
            assert len(p["orig"]) == len(p["patched"])
            data = data.replace(p["orig"], p["patched"])
    args.asar.write_bytes(data)

    if fpending:
        asar = Asar.load(args.asar)
        wanted = {e["path"]: e["content"] for e in fentries
                  if f"{e['name']}:{e['path']}" in fpending}
        asar.write(args.asar, wanted)

    data = args.asar.read_bytes()
    applied2, pending2, _ = classify(data, pairs)
    fapplied2, fpending2, _ = classify_files(Asar.load(args.asar), fentries)
    if pending2 or fpending2:
        log(f"ERROR still pending after write: {pending2 + fpending2} - restoring backup")
        args.asar.write_bytes(backup.read_bytes())
        return 3
    log(f"done - {len(applied2)}/{len(pairs)} byte, {len(fapplied2)}/{len(fentries)} file "
        f"applied, backup at {backup.name}")

    if was_running:
        start_openwhispr()
    return 0


if __name__ == "__main__":
    sys.exit(main())
