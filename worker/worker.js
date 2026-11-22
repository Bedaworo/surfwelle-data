/* Surfwelle Push - Cloudflare Worker
 *
 * Nimmt Push-Anmeldungen der Seite entgegen und gibt sie an GitHub Actions
 * (notify.js) weiter. Gespeichert wird je Browser nur die Push-Adresse mit
 * den zwei Schlüsseln und den gewählten Einstellungen - kein Name, keine
 * E-Mail, keine IP-Adresse.
 *
 * Bindungen im Cloudflare-Dashboard:
 *   SUBS         KV-Namespace (Variable name: SUBS)
 *   ADMIN_TOKEN  Secret - nur GitHub Actions kennt es (Abruf/Aufräumen)
 *
 * Endpunkte:
 *   POST /subscribe    {subscription, settings}  -> anlegen/aktualisieren
 *   POST /unsubscribe  {endpoint}                -> löschen
 *   GET  /list         (Bearer ADMIN_TOKEN)      -> alle Anmeldungen
 *   POST /remove       (Bearer ADMIN_TOKEN) {ids} -> abgelaufene löschen
 */

const ALLOWED_ORIGINS = ['https://bedaworo.github.io'];
// Nur echte Push-Dienste der Browserhersteller annehmen
const PUSH_HOSTS = [/\.googleapis\.com$/, /\.push\.apple\.com$/, /\.mozilla\.com$/,
                    /\.notify\.windows\.com$/, /^fcm\.googleapis\.com$/];
const MAX_BODY = 4096;

function cors(origin) {
  const ok = ALLOWED_ORIGINS.includes(origin);
  return {
    'Access-Control-Allow-Origin': ok ? origin : ALLOWED_ORIGINS[0],
    'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    'Vary': 'Origin',
  };
}
const json = (data, status, origin) => new Response(JSON.stringify(data), {
  status, headers: { 'Content-Type': 'application/json', ...cors(origin) },
});

async function idOf(endpoint) {
  const h = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(endpoint));
  return [...new Uint8Array(h)].map(b => b.toString(16).padStart(2, '0')).join('');
}

const int = (v, lo, hi, def) => Number.isInteger(v) && v >= lo && v <= hi ? v : def;
function cleanSettings(s) {
  s = s || {};
  const d = s.daily || {}, r = s.run || {}, w = s.warn || {};
  return {
    daily: { on: d.on === true, hour: int(d.hour, 4, 8, 6), minStars: int(d.minStars, 1, 3, 1) },
    run:   { on: r.on === true },
    warn:  { on: w.on === true, minStars: int(w.minStars, 1, 3, 1),
             minP: [50, 60, 70, 80, 90].includes(w.minP) ? w.minP : 50 },
  };
}
function validSubscription(sub) {
  if (!sub || typeof sub.endpoint !== 'string' || !sub.keys) return false;
  if (typeof sub.keys.p256dh !== 'string' || typeof sub.keys.auth !== 'string') return false;
  let u; try { u = new URL(sub.endpoint); } catch (e) { return false; }
  return u.protocol === 'https:' && PUSH_HOSTS.some(rx => rx.test(u.hostname));
}

async function readBody(req) {
  const text = await req.text();
  if (text.length > MAX_BODY) throw new Error('zu groß');
  return JSON.parse(text);
}
const isAdmin = (req, env) => env.ADMIN_TOKEN &&
  req.headers.get('Authorization') === `Bearer ${env.ADMIN_TOKEN}`;

export default {
  async fetch(req, env) {
    const origin = req.headers.get('Origin') || '';
    const url = new URL(req.url);
    if (req.method === 'OPTIONS') return new Response(null, { headers: cors(origin) });

    try {
      if (url.pathname === '/subscribe' && req.method === 'POST') {
        if (!ALLOWED_ORIGINS.includes(origin)) return json({ error: 'origin' }, 403, origin);
        const body = await readBody(req);
        if (!validSubscription(body.subscription)) return json({ error: 'subscription' }, 400, origin);
        const sub = { endpoint: body.subscription.endpoint,
                      keys: { p256dh: body.subscription.keys.p256dh, auth: body.subscription.keys.auth } };
        const id = await idOf(sub.endpoint);
        const settings = cleanSettings(body.settings);
        if (!settings.daily.on && !settings.run.on && !settings.warn.on) {
          await env.SUBS.delete(id);                // alles aus = abgemeldet
          return json({ ok: true, removed: true }, 200, origin);
        }
        await env.SUBS.put(id, JSON.stringify({ sub, settings, updated: new Date().toISOString().slice(0, 10) }));
        return json({ ok: true, id }, 200, origin);
      }

      if (url.pathname === '/unsubscribe' && req.method === 'POST') {
        if (!ALLOWED_ORIGINS.includes(origin)) return json({ error: 'origin' }, 403, origin);
        const body = await readBody(req);
        if (typeof body.endpoint !== 'string') return json({ error: 'endpoint' }, 400, origin);
        await env.SUBS.delete(await idOf(body.endpoint));
        return json({ ok: true }, 200, origin);
      }

      if (url.pathname === '/list' && req.method === 'GET') {
        if (!isAdmin(req, env)) return json({ error: 'auth' }, 401, origin);
        const out = [];
        let cursor;
        do {
          const page = await env.SUBS.list({ cursor });
          for (const k of page.keys) {
            const v = await env.SUBS.get(k.name, 'json');
            if (v) out.push({ id: k.name, ...v });
          }
          cursor = page.list_complete ? null : page.cursor;
        } while (cursor);
        return json({ subs: out }, 200, origin);
      }

      if (url.pathname === '/remove' && req.method === 'POST') {
        if (!isAdmin(req, env)) return json({ error: 'auth' }, 401, origin);
        const body = await readBody(req);
        const ids = Array.isArray(body.ids) ? body.ids.filter(x => /^[0-9a-f]{64}$/.test(x)) : [];
        for (const id of ids) await env.SUBS.delete(id);
        return json({ ok: true, removed: ids.length }, 200, origin);
      }

      if (url.pathname === '/' ) return json({ ok: true, service: 'surfwelle-push' }, 200, origin);
      return json({ error: 'not found' }, 404, origin);
    } catch (e) {
      return json({ error: 'bad request' }, 400, origin);
    }
  },
};
