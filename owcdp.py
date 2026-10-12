"""Tiny CDP helper: evaluate an expression in the OpenWhispr renderer that has electronAPI."""
import json
import sys
import urllib.request

try:
    import websocket
except ImportError:
    raise SystemExit("ERROR: the 'websocket-client' package is missing. "
                     "Install it with:  py -m pip install websocket-client")


class Renderer:
    def __init__(self, port=9222):
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
            if self.ev("typeof window.electronAPI?.whisperServerStatus") == "function":
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


if __name__ == "__main__":
    port = 9222
    args = sys.argv[1:]
    if args and args[0] == "--port":
        port = int(args[1])
        args = args[2:]
    r = Renderer(port)
    print(json.dumps(r.ev(args[0], await_promise=True), indent=2, default=str))
