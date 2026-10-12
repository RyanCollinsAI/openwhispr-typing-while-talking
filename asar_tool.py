"""Read and rewrite an Electron asar archive, so a patch can replace whole files.

The existing patch mechanism swaps bytes in place and asserts the replacement is
the same length, which is fine for flipping a Tailwind class or a comparison
operator. Anything that adds real code needs more room than that, so this
rebuilds the archive with corrected offsets instead.

Layout, verified against OpenWhispr 1.7.6's app.asar:

    0   uint32  4                    payload length of the size pickle
    4   uint32  headerPickleSize     = 8 + strLen + pad
    8   uint32  4 + strLen + pad
    12  uint32  strLen               length of the JSON directory
    16  ...     JSON, then pad to a 4-byte boundary
    8 + headerPickleSize             first byte of file data

Every file entry carries a byte offset relative to that data base. Changing one
file's size shifts every later offset, which is why this rewrites the whole
header rather than editing in place.

Entries marked `unpacked` live on disk beside the archive (app.asar.unpacked)
and hold no data here, so they are carried through untouched.

    py asar_tool.py list  <asar> [substring]
    py asar_tool.py read  <asar> <path-in-asar> <out-file>
    py asar_tool.py verify <asar-a> <asar-b>     compare every file's bytes
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path


def _align4(n: int) -> int:
    return n + ((4 - n % 4) % 4)


class Asar:
    def __init__(self, header: dict, data: bytes):
        self.header = header
        self.data = data

    # ---------------------------------------------------------------- reading

    @classmethod
    def load(cls, path) -> "Asar":
        if not Path(path).is_file():
            raise SystemExit(f"ERROR: app archive not found: {path}")
        blob = Path(path).read_bytes()
        _, header_pickle, _, str_len = struct.unpack("<IIII", blob[:16])
        header = json.loads(blob[16:16 + str_len].decode("utf-8"))
        base = 8 + header_pickle
        return cls(header, blob[base:])

    def _entry(self, path_in_asar: str):
        node = self.header
        parts = [p for p in path_in_asar.replace("\\", "/").split("/") if p]
        for part in parts[:-1]:
            node = node["files"][part]
        return node["files"][parts[-1]]

    def read(self, path_in_asar: str) -> bytes:
        meta = self._entry(path_in_asar)
        if meta.get("unpacked"):
            raise KeyError(f"{path_in_asar} is unpacked, not stored in the archive")
        off = int(meta["offset"])
        return self.data[off:off + int(meta["size"])]

    def walk(self):
        """Yield (path, meta) for every file entry, in header order."""
        def rec(node, prefix):
            for name, meta in node.get("files", {}).items():
                p = f"{prefix}/{name}" if prefix else name
                if "files" in meta:
                    yield from rec(meta, p)
                else:
                    yield p, meta
        yield from rec(self.header, "")

    # ---------------------------------------------------------------- writing

    def write(self, out_path, replacements: dict[str, bytes]):
        """Rebuild the archive, substituting `replacements` (path -> new bytes).

        File order is preserved so an unchanged archive round-trips to the same
        bytes; only sizes and offsets move.
        """
        wanted = {k.replace("\\", "/"): v for k, v in replacements.items()}
        seen = set()
        chunks: list[bytes] = []
        cursor = 0

        for path, meta in self.walk():
            if meta.get("unpacked"):
                continue
            if path in wanted:
                blob = wanted[path]
                seen.add(path)
            else:
                off = int(meta["offset"])
                blob = self.data[off:off + int(meta["size"])]
            meta["offset"] = str(cursor)
            meta["size"] = len(blob)
            chunks.append(blob)
            cursor += len(blob)

        missing = set(wanted) - seen
        if missing:
            raise KeyError(f"not present in archive: {sorted(missing)}")

        js = json.dumps(self.header, separators=(",", ":")).encode("utf-8")
        str_len = len(js)
        pad = _align4(str_len) - str_len
        header_pickle = 8 + str_len + pad
        out = bytearray()
        out += struct.pack("<IIII", 4, header_pickle, 4 + str_len + pad, str_len)
        out += js
        out += b"\0" * pad
        for c in chunks:
            out += c
        Path(out_path).write_bytes(bytes(out))
        return len(seen)


# ------------------------------------------------------------------------ cli

def _cli(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    cmd, target = argv[1], argv[2]

    if cmd == "list":
        needle = argv[3] if len(argv) > 3 else ""
        a = Asar.load(target)
        n = 0
        for path, meta in a.walk():
            if needle and needle not in path:
                continue
            flag = " (unpacked)" if meta.get("unpacked") else ""
            print(f"{meta.get('size', 0):>10}  {path}{flag}")
            n += 1
        print(f"-- {n} entries")
        return 0

    if cmd == "read":
        a = Asar.load(target)
        Path(argv[4]).write_bytes(a.read(argv[3]))
        print(f"wrote {argv[4]}")
        return 0

    if cmd == "verify":
        a, b = Asar.load(target), Asar.load(argv[3])
        amap = {p: m for p, m in a.walk()}
        bmap = {p: m for p, m in b.walk()}
        if set(amap) != set(bmap):
            only_a = sorted(set(amap) - set(bmap))
            only_b = sorted(set(bmap) - set(amap))
            print(f"FAIL different file sets; only in A: {only_a[:5]}, "
                  f"only in B: {only_b[:5]}")
            return 1
        diff = []
        for p in amap:
            if amap[p].get("unpacked"):
                continue
            if a.read(p) != b.read(p):
                diff.append(p)
        if diff:
            print(f"{len(diff)} file(s) differ:")
            for p in diff[:20]:
                print(f"  {p}")
        else:
            print("identical: every file's bytes match")
        print(f"-- {len(amap)} entries compared")
        return 0

    print(f"unknown command {cmd!r}")
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
