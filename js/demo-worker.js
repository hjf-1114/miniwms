/* Run the same Flask API against a browser-local copy of the synthetic demo database. */
const runtimeUrl = "https://cdn.jsdelivr.net/pyodide/v0.28.3/full/";
const dbPath = "/miniwms-demo.db";
let runtime;
let pending = Promise.resolve();

const storage = new Promise((resolve, reject) => {
  const request = indexedDB.open("miniwms-demo", 1);
  request.onupgradeneeded = () => request.result.createObjectStore("database");
  request.onsuccess = () => resolve(request.result);
  request.onerror = () => reject(request.error);
});

async function savedDatabase() {
  const db = await storage;
  return new Promise((resolve, reject) => {
    const request = db.transaction("database").objectStore("database").get("sqlite");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function persist(bytes) {
  const db = await storage;
  await new Promise((resolve, reject) => {
    const tx = db.transaction("database", "readwrite");
    tx.objectStore("database").put(bytes, "sqlite");
    tx.oncomplete = resolve;
    tx.onabort = () => reject(tx.error);
  });
}

async function asset(filename) {
  const url = new URL(filename, self.location.href);
  url.search = self.location.search;
  const response = await fetch(url);
  if (!response.ok) throw new Error("演示资源加载失败：" + filename);
  return new Uint8Array(await response.arrayBuffer());
}

async function initialize() {
  importScripts(runtimeUrl + "pyodide.js");
  const py = await loadPyodide({ indexURL: runtimeUrl });
  await py.loadPackage(["sqlite3", "micropip", "markupsafe"]);
  await py.runPythonAsync('import micropip\nawait micropip.install("flask==3.1.2")');
  py.unpackArchive(await asset("server.zip"), "zip", { extractDir: "/miniwms-app" });
  py.FS.writeFile(dbPath, await savedDatabase() || await asset("sample-db.bin"));
  py.runPython(`
import json
import os
import sys

sys.path.insert(0, "/miniwms-app")
os.environ["MINIWMS_DB"] = "/miniwms-demo.db"
os.environ["MINIWMS_ALLOW_ANONYMOUS"] = "1"

import main
main.init_db()

with main.app.test_client() as client:
    token = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).get_json()["token"]
    sample_warehouse = next(w["id"] for w in client.get("/api/warehouses").get_json()["warehouses"]
                            if w["code"] == "DEMO-PA-WH")

def demo_request(raw):
    req = json.loads(raw)
    headers = {"Authorization": "Bearer " + token,
               "X-WMS-Warehouse": str(req["warehouse"] or sample_warehouse)}
    with main.app.test_client() as client:
        response = client.open(req["path"], method=req["method"], json=req["body"], headers=headers)
        return json.dumps({"status": response.status_code, "data": response.get_json(silent=True)})
`);
  return py;
}

function listen(target) {
  target.addEventListener("message", event => {
    const request = event.data;
    pending = pending.then(async () => {
      try {
        const py = await (runtime ||= initialize());
        const handle = py.globals.get("demo_request");
        let outcome;
        try { outcome = JSON.parse(handle(JSON.stringify(request))); }
        finally { handle.destroy(); }
        if ((request.method !== "GET" || request.path.startsWith("/api/putaway/recommend?"))
            && outcome.status < 400) {
          await persist(py.FS.readFile(dbPath));
        }
        target.postMessage({ id: request.id, ...outcome });
      } catch (e) {
        target.postMessage({ id: request.id, status: 500, data: { error: "演示环境暂不可用：" + e.message } });
      }
    });
  });
}

if ("onconnect" in self) {
  self.onconnect = event => {
    const port = event.ports[0];
    listen(port);
    port.start();
  };
} else {
  listen(self);
}
