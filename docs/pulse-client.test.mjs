// Kurzer Test für pulse-client.js. Aufruf (Adressen/Schlüssel kommen aus der Umgebung, hier nur Test-Werte):
//   PULSE_LOCAL_URL=http://127.0.0.1:8765 PULSE_TOKEN=… [PULSE_CLOUD_URL=… PULSE_CLOUD_KEY=…] node docs/pulse-client.test.mjs
// tests/test_sync.py startet Wegwerf-Server und ruft dies automatisch auf.
import assert from "node:assert/strict";
import { createLocalClient, createCloudClient, PulseApiError } from "./pulse-client.js";

const local = createLocalClient({ baseUrl: process.env.PULSE_LOCAL_URL, token: process.env.PULSE_TOKEN });

const health = await local.health();
assert.equal(health.ok, true);
assert.equal(health.authenticated, true);

const stats = await local.stats();
assert.equal(stats.videos.total, 4);

const list = await local.listVideos({ platform: "tiktok", limit: 2, sort: "view_count", order: "asc" });
assert.equal(list.items.length, 2);
assert.equal(list.total, 4);
assert.deepEqual(list.items[0].hashtags, ["a", "b"]);

const uid = list.items[0].uid; // enthält ":" – wird URL-kodiert
const video = await local.getVideo(uid);
assert.equal(video.uid, uid);
assert.equal((await local.listComments(uid)).items.length, 2);
assert.equal((await local.history(uid)).items.length, 1);
assert.equal((await local.newComments("2000-01-01T00:00:00Z")).total, 8);
assert.equal((await local.listGroups()).items[0].name, "Sammlung");
assert.ok((await local.listAccounts()).items.length >= 1);
assert.equal((await local.listVideos({ group: "Sammlung" })).total, 2);

await assert.rejects(local.getVideo("tiktok:gibtesnicht"), (e) => e instanceof PulseApiError && e.status === 404);
await assert.rejects(local.listVideos({ platform: "myspace" }), (e) => e.status === 400 && e.code === "bad_request");
await assert.rejects(
  createLocalClient({ baseUrl: process.env.PULSE_LOCAL_URL, token: "falsch" }).stats(),
  (e) => e.status === 401,
);
await assert.rejects(
  createLocalClient({ baseUrl: "http://127.0.0.1:1", token: "x" }).stats(),
  (e) => e.status === 0 && e.code === "network",
);
assert.equal((await local.listVideos({ limit: 99999 })).limit, 500); // Begrenzung im Client

if (process.env.PULSE_CLOUD_URL) {
  const cloud = createCloudClient({ url: process.env.PULSE_CLOUD_URL, readKey: process.env.PULSE_CLOUD_KEY });
  assert.equal((await cloud.health()).ok, true);
  const page = await cloud.listVideos({ limit: 2, platform: "tiktok" });
  assert.equal(page.items.length, 2);
  assert.equal(page.total, 4);
  assert.equal((await cloud.listVideos({ group: "Sammlung" })).total, 2);
  assert.equal((await cloud.listVideos({ group: "Gibt es nicht" })).total, 0);
  const cuid = page.items[0].uid;
  assert.equal((await cloud.getVideo(cuid)).uid, cuid);
  assert.equal((await cloud.listComments(cuid)).items.length, 2);
  assert.equal((await cloud.history(cuid)).items.length, 1);
  assert.equal((await cloud.newComments("2000-01-01T00:00:00Z")).items.length, 8);
  assert.equal((await cloud.listGroups()).items[0].name, "Sammlung");
  assert.equal((await cloud.stats()).videos.total, 4);
  await assert.rejects(cloud.getVideo("tiktok:nein"), (e) => e.status === 404);
  await assert.rejects(
    createCloudClient({ url: process.env.PULSE_CLOUD_URL, readKey: "falsch" }).listVideos(),
    (e) => e.status === 401,
  );
}
console.log("pulse-client: alle Prüfungen bestanden");
