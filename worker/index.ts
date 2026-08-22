/** Cloudflare Worker entry point for the Meccanoid dashboard. */
import { handleImageOptimization, DEFAULT_DEVICE_SIZES, DEFAULT_IMAGE_SIZES } from "vinext/server/image-optimization";
import handler from "vinext/server/app-router-entry";

interface Env {
  ASSETS: Fetcher;
  DB: D1Database;
  IMAGES: {
    input(stream: ReadableStream): {
      transform(options: Record<string, unknown>): {
        output(options: { format: string; quality: number }): Promise<{ response(): Response }>;
      };
    };
  };
}
interface ExecutionContext { waitUntil(promise: Promise<unknown>): void; passThroughOnException(): void }
const SESSION_PATTERN = /^[0-9a-f-]{36}$/i;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" }
});

async function eventsApi(request: Request, env: Env): Promise<Response> {
  if (request.method === "GET") {
    const sessionId = new URL(request.url).searchParams.get("session") || "";
    if (!SESSION_PATTERN.test(sessionId)) return json({ error: "Invalid session" }, 400);
    const result = await env.DB.prepare(
      "SELECT id, kind, role, message, latency_ms AS latencyMs, created_at AS createdAt FROM robot_events WHERE session_id = ? ORDER BY created_at DESC, id DESC LIMIT 100"
    ).bind(sessionId).all();
    return json({ events: result.results });
  }
  if (request.method === "POST") {
    const body = await request.json().catch(() => null) as Record<string, unknown> | null;
    const sessionId = typeof body?.sessionId === "string" ? body.sessionId : "";
    const kind = typeof body?.kind === "string" ? body.kind : "";
    const role = typeof body?.role === "string" ? body.role : "";
    const message = typeof body?.message === "string" ? body.message.trim() : "";
    const latency = Number.isInteger(body?.latencyMs) ? Number(body?.latencyMs) : null;
    if (!SESSION_PATTERN.test(sessionId) || !["chat","command","error"].includes(kind) ||
        !["you","bot","system"].includes(role) || !message || message.length > 1000 ||
        (latency !== null && (latency < 0 || latency > 300000))) {
      return json({ error: "Invalid event" }, 400);
    }
    const now = Date.now();
    await env.DB.batch([
      env.DB.prepare("INSERT INTO robot_events (session_id, kind, role, message, latency_ms, created_at) VALUES (?, ?, ?, ?, ?, ?)")
        .bind(sessionId, kind, role, message, latency, now),
      env.DB.prepare("DELETE FROM robot_events WHERE session_id = ? AND id NOT IN (SELECT id FROM robot_events WHERE session_id = ? ORDER BY created_at DESC, id DESC LIMIT 100)")
        .bind(sessionId, sessionId),
    ]);
    return json({ ok: true }, 201);
  }
  return json({ error: "Method not allowed" }, 405);
}

const worker = {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/api/events") return eventsApi(request, env);
    if (url.pathname === "/_vinext/image") {
      const allowedWidths = [...DEFAULT_DEVICE_SIZES, ...DEFAULT_IMAGE_SIZES];
      return handleImageOptimization(request, {
        fetchAsset: (path) => env.ASSETS.fetch(new Request(new URL(path, request.url))),
        transformImage: async (body, { width, format, quality }) => {
          const result = await env.IMAGES.input(body).transform(width > 0 ? { width } : {}).output({ format, quality });
          return result.response();
        },
      }, allowedWidths);
    }
    return handler.fetch(request, env, ctx);
  },
};
export default worker;
