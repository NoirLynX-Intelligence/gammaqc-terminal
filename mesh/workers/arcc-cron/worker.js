// arcc-cron — Pre-Market Collision Matrix scheduler.
//
// Two cron triggers per day (UTC, no DST drift):
//   13:25 UTC = 08:25 ET STANDARD  / 09:25 EDT      → PRIMARY
//   13:35 UTC = 08:35 ET STANDARD  / 09:35 EDT      → WATCHDOG
//   12:25 UTC = 08:25 EDT          / 07:25 ET STD   → PRIMARY (DST)
//   12:35 UTC = 08:35 EDT          / 07:35 ET STD   → WATCHDOG (DST)
//
// We register all 4 — the backend's idempotency check (already_ran flag
// in the cron_state collection) ensures we only audit once per
// collision_date, regardless of which trigger fires first. This way we
// get the same "08:25 ET local time" regardless of which side of the
// DST cutover we're on.
//
// PRIMARY behavior: POST /api/oracle/cron/collision-matrix.
//   If the backend says already_ran=true (e.g. watchdog beat primary
//   due to a fly cold start), that's fine — we logged it and move on.
//
// WATCHDOG behavior: GET /api/oracle/cron/status.
//   If ran_today=false, the primary missed — fire the same POST as
//   primary would. This is the failover Commander requested in the spec.
//
// Both flows authenticate with a shared Bearer token (ARCC_CRON_TOKEN
// secret) and report failures to ops via a separate notification path.

const PRIMARY_PATH = "/api/oracle/cron/collision-matrix";
const STATUS_PATH = "/api/oracle/cron/status";

async function fireCron(env, role) {
  const url = (env.BACKEND_BASE || "https://api.gammaqc.com").replace(/\/+$/, "");
  const token = env.ARCC_CRON_TOKEN;
  if (!token) {
    console.error("[arcc-cron] ARCC_CRON_TOKEN unset — refusing to fire");
    return { ok: false, error: "no_token" };
  }
  const startedAt = new Date().toISOString();
  let result;

  if (role === "primary") {
    // Direct POST — backend handles idempotency
    const r = await fetch(url + PRIMARY_PATH, {
      method: "POST",
      headers: {
        "Authorization": "Bearer " + token,
        "content-type": "application/json",
      },
      body: "{}",
    });
    const body = await r.text();
    result = {
      ok: r.ok,
      status: r.status,
      role: "primary",
      response: body.slice(0, 500),
      startedAt,
    };
  } else if (role === "watchdog") {
    // Check first, then fire if needed
    const s = await fetch(url + STATUS_PATH, {
      headers: { "Authorization": "Bearer " + token },
    });
    let ranToday = false;
    if (s.ok) {
      try {
        const j = await s.json();
        ranToday = !!j.ran_today;
      } catch (e) { /* parse fail → treat as not-ran, fire safety */ }
    }
    if (ranToday) {
      result = { ok: true, role: "watchdog", action: "skipped", reason: "primary_ran", startedAt };
    } else {
      const r = await fetch(url + PRIMARY_PATH, {
        method: "POST",
        headers: {
          "Authorization": "Bearer " + token,
          "content-type": "application/json",
        },
        body: "{}",
      });
      const body = await r.text();
      result = {
        ok: r.ok,
        status: r.status,
        role: "watchdog",
        action: "failover_fired",
        response: body.slice(0, 500),
        startedAt,
      };
    }
  } else {
    result = { ok: false, role, error: "unknown_role" };
  }
  console.log("[arcc-cron]", JSON.stringify(result));
  return result;
}

// Determine which role this cron firing represents based on the minute.
// :25 = primary, :35 = watchdog. (We could also key off the cron expression
// itself via event.cron, which is more robust to schedule changes.)
function roleFromEvent(event) {
  const cron = event.cron || "";
  if (cron.includes("25 12") || cron.includes("25 13")) return "primary";
  if (cron.includes("35 12") || cron.includes("35 13")) return "watchdog";
  // Fallback: time-of-day
  const min = new Date(event.scheduledTime).getUTCMinutes();
  if (min < 30) return "primary";
  return "watchdog";
}

export default {
  async scheduled(event, env, ctx) {
    const role = roleFromEvent(event);
    ctx.waitUntil(fireCron(env, role));
  },
  // Manual trigger surface — auth-gated, lets ops kick a run from curl
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/healthz") {
      return new Response("ok", { headers: { "content-type": "text/plain" } });
    }
    if (url.pathname === "/trigger") {
      const auth = request.headers.get("authorization") || "";
      if (auth !== "Bearer " + (env.ARCC_CRON_TOKEN || "__unset__")) {
        return new Response("unauthorized", { status: 401 });
      }
      const role = url.searchParams.get("role") || "primary";
      const result = await fireCron(env, role);
      return new Response(JSON.stringify(result, null, 2), {
        status: result.ok ? 200 : 502,
        headers: { "content-type": "application/json" },
      });
    }
    return new Response(JSON.stringify({
      service: "arcc-cron",
      endpoints: ["/healthz", "/trigger?role=primary|watchdog"],
    }), { status: 404, headers: { "content-type": "application/json" } });
  },
};
