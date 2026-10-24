// pulse-client.js – kleiner Client für die Daten von Socialscraper (ES-Modul, ohne Abhängigkeiten).
//
// Zwei Wege, gleiche Antwortform ({ schema_version, items, total, limit, offset }):
//   1) Lokal : createLocalClient({ baseUrl: "http://127.0.0.1:8765", token })   -> lokale Schnittstelle (nur lesend)
//   2) Cloud : createCloudClient({ url, readKey })                              -> Supabase/PostgREST (nur LESE-Schlüssel!)
//
// SICHERHEIT: Der Token der lokalen Schnittstelle und der Cloud-Schlüssel gehören NICHT in Code, der im Browser einer
// öffentlichen Webseite landet. Lokal: PULSE läuft auf demselben Rechner (Entwicklung) oder ein eigenes Backend hält den
// Token. Cloud: nur der "anon"-Schlüssel mit Row-Level-Security (nur Lesen), nie der service_role-Schlüssel.
//
// Beispiel (lokal):
//   import { createLocalClient } from "./pulse-client.js";
//   const api = createLocalClient({ baseUrl: "http://127.0.0.1:8765", token: "…" });
//   const { items } = await api.listVideos({ platform: "tiktok", limit: 20 });
//   const history = await api.history(items[0].uid);
//
// Beispiel (Cloud):
//   const cloud = createCloudClient({ url: "https://xyz.supabase.co", readKey: "<anon key>" });
//   const { items } = await cloud.listVideos({ group: "Sammlung", q: "tanz" });

export const SCHEMA_VERSION = 1;
export const MAX_LIMIT = 500;

export class PulseApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.name = "PulseApiError";
    this.status = status; // HTTP-Status, 0 = Netzfehler
    this.code = code;
  }
}

function qs(params) {
  const sp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") sp.set(key, String(value));
  }
  const text = sp.toString();
  return text ? `?${text}` : "";
}

async function call(fetchImpl, url, init, errorFrom) {
  let response;
  try {
    response = await fetchImpl(url, init);
  } catch {
    throw new PulseApiError(0, "network", "Keine Verbindung. Läuft Socialscraper bzw. ist die Cloud erreichbar?");
  }
  let body = null;
  const text = await response.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!response.ok) throw errorFrom(response.status, body);
  return { body, response };
}

function clampLimit(limit) {
  const n = Number.isFinite(Number(limit)) ? Math.trunc(Number(limit)) : 100;
  return Math.min(Math.max(n, 1), MAX_LIMIT);
}

// ---------------------------------------------------------------- lokal

export function createLocalClient({ baseUrl, token, fetch: fetchImpl = globalThis.fetch }) {
  if (!baseUrl) throw new Error("baseUrl fehlt");
  const root = baseUrl.replace(/\/+$/, "") + "/v1";
  const headers = token ? { Authorization: `Bearer ${token}`, Accept: "application/json" } : { Accept: "application/json" };
  const errorFrom = (status, body) =>
    new PulseApiError(status, body?.error?.code ?? "error", body?.error?.message ?? `Fehler ${status}`);
  const get = async (path, params = {}) => (await call(fetchImpl, root + path + qs(params), { headers }, errorFrom)).body;
  const uidPath = (uid) => encodeURIComponent(uid);

  return {
    kind: "local",
    health: () => get("/health"), // braucht keinen Token; "authenticated" zeigt, ob der Token stimmt
    stats: () => get("/stats"),
    listVideos: ({ platform, group, q, since, sort, order, limit = 100, offset = 0 } = {}) =>
      get("/videos", { platform, group, q, since, sort, order, limit: clampLimit(limit), offset }),
    getVideo: async (uid, { raw = false } = {}) => (await get(`/videos/${uidPath(uid)}`, { include: raw ? "raw" : undefined })).item,
    listComments: (uid, { q, since, parent, sort, order, limit = 100, offset = 0 } = {}) =>
      get(`/videos/${uidPath(uid)}/comments`, { q, since, parent, sort, order, limit: clampLimit(limit), offset }),
    history: (uid, { since, limit = 500, offset = 0 } = {}) =>
      get(`/videos/${uidPath(uid)}/history`, { since, limit: clampLimit(limit), offset }),
    newComments: (since, { platform, limit = 100, offset = 0 } = {}) =>
      get("/comments/new", { since, platform, limit: clampLimit(limit), offset }),
    listGroups: () => get("/groups"),
    listAccounts: () => get("/accounts"),
  };
}

// ---------------------------------------------------------------- Cloud (Supabase / PostgREST)

const VIDEO_FIELDS = "uid,platform,video_id,url,author,author_id,caption,created_at,duration_s,view_count,like_count,comment_count,share_count,save_count,hashtags,mentions,music,language,thumbnail,first_seen_at,fetched_at";
const COMMENT_FIELDS = "uid,platform,video_id,comment_id,text,author,author_id,created_at,like_count,reply_count,parent_id,is_author_reply,language,first_seen_at,fetched_at";

// In Listen (in.(…)) müssen Werte mit Sonderzeichen in Anführungszeichen stehen; bei eq.… nicht.
const pg = (text) => `"${String(text).replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
const ilike = (text) => String(text).replace(/[%_*,()]/g, " ").trim();

export function createCloudClient({ url, readKey, fetch: fetchImpl = globalThis.fetch }) {
  if (!url || !readKey) throw new Error("url und readKey sind nötig");
  const root = url.replace(/\/+$/, "").replace(/\/rest\/v1$/, "") + "/rest/v1";
  const headers = { apikey: readKey, Authorization: `Bearer ${readKey}`, Accept: "application/json" };
  const errorFrom = (status, body) =>
    new PulseApiError(status, body?.code ?? "error",
      status === 401 || status === 403 ? "Die Cloud lehnt den Schlüssel ab." : body?.message ?? `Fehler ${status}`);

  async function select(table, params, { count = false } = {}) {
    const { body, response } = await call(
      fetchImpl,
      `${root}/${table}${qs(params)}`,
      { headers: count ? { ...headers, Prefer: "count=exact" } : headers },
      errorFrom,
    );
    const range = response.headers.get("content-range"); // z. B. "0-19/134"
    const total = range && /\/(\d+)$/.test(range) ? Number(range.split("/")[1]) : null;
    return { rows: Array.isArray(body) ? body : [], total };
  }

  const splitUid = (uid) => {
    const i = uid.indexOf(":");
    if (i < 1) throw new PulseApiError(400, "bad_request", "Ungültige Video-ID (erwartet plattform:id).");
    return { platform: uid.slice(0, i), video_id: uid.slice(i + 1) };
  };
  const page = (rows, total, limit, offset) => ({ schema_version: SCHEMA_VERSION, items: rows, total: total ?? rows.length, limit, offset });

  async function groupUids(group) {
    const { rows } = await select("group_videos", { select: "video_uid", group_name: `eq.${group}`, limit: 10000 });
    return rows.map((r) => r.video_uid);
  }

  return {
    kind: "cloud",
    async health() {
      await select("videos", { select: "uid", limit: 1 });
      return { schema_version: SCHEMA_VERSION, ok: true };
    },
    async stats() {
      const count = async (table, key) => (await select(table, { select: key, limit: 1 }, { count: true })).total ?? 0;
      return {
        schema_version: SCHEMA_VERSION,
        videos: { total: await count("videos", "uid") },
        comments: { total: await count("comments", "uid") },
        snapshots: await count("video_snapshots", "video_uid"),
        groups: await count("groups", "name"),
      };
    },
    async listVideos({ platform, group, q, since, sort = "fetched_at", order = "desc", limit = 100, offset = 0 } = {}) {
      limit = clampLimit(limit);
      const params = { select: VIDEO_FIELDS, order: `${sort}.${order},uid.asc`, limit, offset };
      if (platform) params.platform = `eq.${platform}`;
      if (since) params.fetched_at = `gte.${since}`;
      if (q && ilike(q)) params.or = `(caption.ilike.*${ilike(q)}*,author.ilike.*${ilike(q)}*)`;
      if (group) {
        const uids = await groupUids(group);
        if (!uids.length) return page([], 0, limit, offset);
        params.uid = `in.(${uids.map(pg).join(",")})`;
      }
      const { rows, total } = await select("videos", params, { count: true });
      return page(rows, total, limit, offset);
    },
    async getVideo(uid) {
      const { rows } = await select("videos", { select: VIDEO_FIELDS, uid: `eq.${uid}`, limit: 1 });
      if (!rows.length) throw new PulseApiError(404, "not_found", "Dieses Video gibt es nicht.");
      return rows[0];
    },
    async listComments(uid, { q, since, limit = 100, offset = 0 } = {}) {
      limit = clampLimit(limit);
      const { platform, video_id } = splitUid(uid);
      const params = { select: COMMENT_FIELDS, platform: `eq.${platform}`, video_id: `eq.${video_id}`, order: "created_at.desc,uid.asc", limit, offset };
      if (since) params.first_seen_at = `gte.${since}`;
      if (q && ilike(q)) params.text = `ilike.*${ilike(q)}*`;
      const { rows, total } = await select("comments", params, { count: true });
      return { ...page(rows, total, limit, offset), video_uid: uid };
    },
    async history(uid, { since, limit = 500, offset = 0 } = {}) {
      limit = clampLimit(limit);
      const params = { select: "captured_at,view_count,like_count,comment_count,share_count", video_uid: `eq.${uid}`, order: "captured_at.asc", limit, offset };
      if (since) params.captured_at = `gte.${since}`;
      const { rows, total } = await select("video_snapshots", params, { count: true });
      return { ...page(rows, total, limit, offset), video_uid: uid };
    },
    async newComments(since, { platform, limit = 100, offset = 0 } = {}) {
      if (!since) throw new PulseApiError(400, "bad_request", "since fehlt.");
      limit = clampLimit(limit);
      const params = { select: COMMENT_FIELDS, first_seen_at: `gte.${since}`, order: "first_seen_at.desc,uid.asc", limit, offset };
      if (platform) params.platform = `eq.${platform}`;
      const { rows, total } = await select("comments", params, { count: true });
      return page(rows.map((r) => ({ ...r, video_uid: `${r.platform}:${r.video_id}` })), total, limit, offset);
    },
    async listGroups() {
      const { rows } = await select("groups", { select: "name,color,note,created_at", order: "name.asc" });
      return { schema_version: SCHEMA_VERSION, items: rows, total: rows.length };
    },
  };
}
