"""Recompute file-patches-<version>.json after editing anything in files-<version>/.

base_sha256 identifies the stock file this patch was written against, so it is
taken from a pristine archive - app.asar.bak-<version> if it is there, otherwise
the live one, which is right only while the file patch has not been applied yet.
patched_sha256 is just a corruption check on the replacement.

    py refresh_manifest.py
"""
import hashlib
import json
import os
import sys
from pathlib import Path

from asar_tool import Asar

HERE = Path(__file__).resolve().parent
LOCAL_APPDATA = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
APP = LOCAL_APPDATA / "Programs" / "OpenWhispr" / "resources"


def main():
    manifests = sorted(HERE.glob("file-patches-*.json"))
    if not manifests:
        raise SystemExit(f"ERROR: no file-patches-*.json found in {HERE}")
    path = manifests[-1]
    entries = json.loads(path.read_text())
    version = path.stem.replace("file-patches-", "")

    pristine = APP / f"app.asar.bak-{version}"
    source = pristine if pristine.exists() else APP / "app.asar"
    if not source.is_file():
        raise SystemExit(f"ERROR: OpenWhispr's app file was not found: {source}. "
                         "Install OpenWhispr 1.7.6 first.")
    print(f"base hashes from {source.name}")
    asar = Asar.load(source)

    for e in entries:
        replacement_path = HERE / e["file"]
        if not replacement_path.is_file():
            raise SystemExit(f"ERROR: replacement file is missing: {replacement_path}")
        replacement = replacement_path.read_bytes()
        base = asar.read(e["path"])
        e["base_sha256"] = hashlib.sha256(base).hexdigest()
        e["base_size"] = len(base)
        e["patched_sha256"] = hashlib.sha256(replacement).hexdigest()
        e["patched_size"] = len(replacement)
        print(f"  {e['path']}  {e['base_size']} -> {e['patched_size']} bytes")

    path.write_text(json.dumps(entries, indent=1) + "\n")
    print(f"wrote {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
