import requests

class SphereError(RuntimeError): pass

class LinkenSphere:
    def __init__(self, api_port=40080, host="127.0.0.1", timeout=60):
        self.base, self.timeout = f"http://{host}:{api_port}", timeout
        self.s = requests.Session()

    def _post(self, path, payload=None):
        r = self.s.post(f"{self.base}{path}", json=payload or {}, timeout=self.timeout)
        if not r.ok: raise SphereError(f"{path} -> {r.status_code}: {r.text[:300]}")
        try: return r.json()
        except ValueError: return {}

    def ping(self):
        try: self.s.get(self.base, timeout=5); return True
        except requests.RequestException: return False

    def use_preset(self, preset_id):
        return self._post("/presets/set_active_provider", {"provider_id": preset_id})

    def create(self, name=None):
        return self._post("/sessions/create_quick", {"name": name} if name else {})["uuid"]

    def start(self, uuid, headless=False, debug_port=None):
        body = {"uuid": uuid, "headless": headless}
        if debug_port: body["debug_port"] = debug_port
        return self._post("/sessions/start", body)["debug_port"]

    def stop(self, uuid):  return self._post("/sessions/stop", {"uuid": uuid})
    def remove(self, uuid): return self._post("/sessions/remove", {"uuid": uuid})
    def set_proxy(self, uuid, proxy_url):
        return self._post("/sessions/update", {"uuid": uuid, "proxy": proxy_url})
