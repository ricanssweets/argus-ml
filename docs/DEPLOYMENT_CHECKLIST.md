# ARGUS — DEPLOYMENT_CHECKLIST

> **Documento escrito a mano, derivado del código real.** Todos los nombres de
> variable de esta página se localizaron con `grep` de `os.environ` /
> `os.environ.get` / `_flag(...)` en `services/ml` y de `process.env` en
> `apps/web`, y se citan con archivo y línea. Lo que no se pudo confirmar está
> marcado como **NO CONFIRMADO** en lugar de suponerse.
>
> Fuentes principales: [`render.yaml`](../render.yaml),
> [`services/ml/start.sh`](../services/ml/start.sh),
> [`services/ml/Dockerfile`](../services/ml/Dockerfile),
> [`services/ml/app/middleware.py`](../services/ml/app/middleware.py),
> [`services/ml/app/authz.py`](../services/ml/app/authz.py),
> [`services/ml/app/migrate.py`](../services/ml/app/migrate.py),
> [`services/ml/app/db.py`](../services/ml/app/db.py),
> [`apps/web/lib/config.ts`](../apps/web/lib/config.ts),
> [`apps/web/next.config.js`](../apps/web/next.config.js),
> [`.env.example`](../.env.example).

---

## Fase A — Desarrollo local

```bash
# Servicio ML (desde la raíz del repo)
export PYTHONPATH=repo/services/ml
export ARGUS_ENV=development        # opt-in explícito de dev (obligatorio con AUTH_DISABLED)
export AUTH_DISABLED=true           # sin esto, hay que enviar JWT en cada petición
export CORS_ORIGINS=http://localhost:3000
unset DATABASE_URL REDIS_URL        # capa stub + limitador en proceso
uvicorn app.main:app --reload --port 8000
```

Si `AUTH_DISABLED=true` y no hay `ARGUS_ENV` de desarrollo ni
`ARGUS_ALLOW_AUTH_DISABLED`, el proceso **se niega a arrancar**: es el guard de
`middleware.assert_auth_configuration()` ejecutado desde el lifespan. Es el
comportamiento correcto, no un bug.

Web:

```bash
cd apps/web
NEXT_PUBLIC_ARGUS_API_URL=http://localhost:8000 npm run dev   # → http://localhost:3000
```

---

## Fase B — Producción (Render / contenedor)

### B.1 Cómo arranca el servicio

1. `preDeployCommand: python -m app.migrate` (definido en
   [`render.yaml`](../render.yaml)) con `MIGRATIONS_FATAL=1`: si una migración
   falla o hay drift de checksum, **el release se bloquea** (esto es intencional).
2. Al arrancar el contenedor, [`start.sh`](../services/ml/start.sh) vuelve a
   ejecutar `python -m app.migrate` **ignorando su código de salida** y después
   hace `exec uvicorn`. Así una base de datos caída no impide que `/health`
   responda.
3. `uvicorn` se ejecuta con `--proxy-headers` y
   `--forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}"`, en
   `${PORT:-8000}`. La resolución real del cliente la hace el middleware a partir
   de `TRUSTED_PROXIES`, no uvicorn.
4. `healthCheckPath: /health`. `/health` **siempre devuelve 200** y declara el
   estado real de la capa de datos (`stub`, `postgres`, `postgres (UNREACHABLE)`).

### B.2 Tabla de variables — servicio ML (`services/ml`)

Leyenda de «Obligatoria»:
**Sí (arranque)** = sin ella no arranca; **Sí (funcional)** = arranca pero una
funcionalidad falla de forma visible; **No** = opcional con degradación explícita.

| Variable | Para qué | Obligatoria | Defecto | Qué se rompe si falta | Fuente |
| --- | --- | --- | --- | --- | --- |
| `DATABASE_URL` | DSN de Postgres (predicciones, observatorio, espejo `svc.*`) | No | *(sin definir)* | Se sirve con la capa stub: `/health` → `data_layer: "stub (UNCONFIRMED demo)"`, las predicciones **no** se persisten, alertas/investigación usan SQLite. Nada falla, pero no hay persistencia | `migrate.py:110`, `db.py:39` |
| `ARGUS_DB_DSN` | Respaldo heredado de `DATABASE_URL` | No | *(sin definir)* | Sólo aplica si `DATABASE_URL` falta; mismo efecto que arriba | `db.py:39` |
| `REDIS_URL` | Rate limit compartido entre réplicas + caché de predicciones | No | *(sin definir)* → limitador en proceso | Con varias réplicas cada una aplica su propio límite (el límite real se multiplica) y no hay caché. Se avisa en el log | `middleware.py:516`, `rate_limit.py:179`, `cache.py:26` |
| `SUPABASE_JWT_SECRET` | Secreto HS256 para verificar los JWT | **Sí (funcional)** con auth activa | *(sin definir)* | **Falla cerrado**: toda ruta protegida responde `503 AUTH_NOT_CONFIGURED` | `authz.py:88`, `middleware.py:373` |
| `SUPABASE_JWT_AUD` | Audiencia esperada del JWT | No | *(sin definir)* → no se valida `aud` | Se aceptan tokens de otra audiencia; se loguea un warning único (`warn_audience_unset`) | `authz.py:96` |
| `SUPABASE_JWT_ISS` | Emisor esperado del JWT | No | *(sin definir)* → no se valida `iss` | Igual que la anterior, para el emisor | `authz.py:101` |
| `SUPABASE_URL` | — | **NO CONFIRMADO** | — | **Ningún código lo lee.** Aparece en `render.yaml:24` y `.env.example`, pero no hay `os.environ`/`process.env` que lo consuma: su propósito real no es verificable desde el repositorio | `render.yaml:24` (declaración) |
| `SUPABASE_ANON_KEY` | — | **NO CONFIRMADO** | — | Igual que `SUPABASE_URL`: declarada, sin consumidor en el código | `render.yaml:26` (declaración) |
| `AUTH_DISABLED` | Desactiva la verificación JWT (identidad fija `dev`) | No (en producción debe ser `"false"`) | *(sin definir)* = auth activa | Si vale `true` sin opt-in dev, **el proceso no arranca**. `render.yaml` fija `"false"` explícitamente | `authz.py:70`, `middleware.py:122`, `render.yaml:38` |
| `ARGUS_ENV` | Marca el proceso como local/dev | No | *(sin definir)* = producción | Es el opt-in que hace aceptable `AUTH_DISABLED=true`, y activa los orígenes CORS de dev. `render.yaml` fija `"production"` | `authz.py:75`, `render.yaml:44` |
| `ARGUS_ALLOW_AUTH_DISABLED` | Opt-in alternativo de dev (`1/true/yes/on`) | No | *(sin definir)* | Sin `ARGUS_ENV` ni esta variable, `AUTH_DISABLED=true` impide arrancar | `authz.py:77` |
| `CORS_ORIGINS` | Allowlist de orígenes del navegador (coma-separada) | **Sí (arranque)** fuera de dev | En dev: `http://localhost:3000`. Fuera de dev: **vacío ⇒ no arranca** | Sin allowlist (o con `*`) fuera de dev, `assert_cors_configuration()` aborta el arranque. Un `*` se descarta siempre porque `allow_credentials=True` | `middleware.py:425,458` |
| `CORS_ALLOW_METHODS` | Métodos CORS permitidos | No | `GET,POST,DELETE,OPTIONS` | Se usa el defecto; los `*` se descartan | `middleware.py:440` |
| `CORS_ALLOW_HEADERS` | Cabeceras CORS permitidas | No | `Authorization,Content-Type,X-Request-ID` | Se usa el defecto | `middleware.py:442` |
| `TRUSTED_PROXIES` | IPs/CIDR de proxies de confianza para `X-Forwarded-For` | No | *(vacío)* | El header se ignora y se usa el par directo: **detrás de un proxy, todos los clientes comparten una única IP** y, por tanto, un único bucket de rate limit (auto-DoS). `render.yaml` lo deja para rellenar en el panel | `middleware.py:184` |
| `FORWARDED_ALLOW_IPS` | Lista de confianza de `uvicorn --proxy-headers` | No | `127.0.0.1` | uvicorn no reescribe el cliente desde `X-Forwarded-For`; nunca debe ser `*` (`render.yaml` lo fija a `127.0.0.1`) | `start.sh:31` (`--forwarded-allow-ips`) |
| `PORT` | Puerto de escucha de uvicorn | No | `8000` | — | `start.sh:29` |
| `MIGRATIONS_FATAL` | Hace fallar (exit 1) el paso de migraciones | No | *(sin definir)* = no fatal | Sin ella, una migración rota se convierte en warning y el servicio arranca igual. En el `preDeployCommand` debe ser `1` | `migrate.py:96`, `render.yaml:56` |
| `ARGUS_MIGRATE_OPTIONAL` | Fuerza modo no fatal aunque `MIGRATIONS_FATAL=1` | No | `0` | Prevalece sobre `MIGRATIONS_FATAL`; con ella el release nunca se bloquea por migraciones | `migrate.py:94` |
| `ARGUS_DB_CONNECT_TIMEOUT` | Timeout de conexión (s) para migraciones y sondas | No | `5` | Se usa el defecto; un valor no numérico también cae al defecto | `migrate.py:103`, `render.yaml:59` |
| `ARGUS_HEALTH_DB_TIMEOUT` | Timeout (s) del sondeo de `/health` | No | `1.5` | Se usa el defecto; debe quedar por debajo del deadline del health check | `main.py:151` |
| `ARGUS_RETRY_SKIPPED` | Reintenta una migración registrada como `skipped` | No | `0` | Sin ella, `007_roles.sql` (u otra que fallara) queda saltada para siempre | `migrate.py:209` |
| `RATE_LIMIT_ANON_PER_MIN` | Límite del bucket anónimo | No | `60` | — | `middleware.py:505` |
| `RATE_LIMIT_AUTH_PER_MIN` | Límite del bucket autenticado | No | `600` | — | `middleware.py:506` |
| `RATE_LIMIT_HEAVY_PER_MIN` | Límite de cómputo pesado | No | `60` | — | `middleware.py:507` |
| `RATE_LIMIT_REDIS_COOLDOWN_S` | Segundos en modo degradado tras un fallo de Redis | No | `10` | Se usa el defecto | `rate_limit.py:66` |
| `PRED_CACHE_TTL` | TTL (s) de la caché de predicciones | No | `300` | Se usa el defecto; un valor no numérico también | `cache.py:31` |
| `PRED_MAX_WORKERS` | Hilos del mapa paralelo de predicciones | No | `4` | Se usa el defecto | `routers/predictions.py:265` |
| `ARGUS_ALERTS_DB` | Ruta alternativa del sidecar SQLite de alertas | No | `services/ml/data/alerts.db` | — | `routers/alerts.py:117` |
| `ARGUS_RESEARCH_DB` | Ruta alternativa del sidecar SQLite de investigación | No | `services/ml/data/research_store.db` | — | `research_store.py:127` |
| `RESEND_API_KEY` | Envío de email (Resend) | No | *(sin definir)* | `POST /api/v1/alerts/{id}/deliver` responde `{"status": "not_configured", "reason": ...}`; la alerta **sigue** en la app | `notifications/email.py:41`, `delivery.py:115` |
| `RESEND_FROM` | Remitente del email | No | `alerts@argus.local` | Con el defecto, Resend rechaza el envío (dominio sin verificar) y se reporta `{"status":"error"}` | `notifications/email.py:45` |
| `ONESIGNAL_API_KEY` | Envío push (OneSignal) | No | *(sin definir)* | `{"status": "not_configured"}`; la alerta sigue en la app | `notifications/push.py:42` |
| `ONESIGNAL_APP_ID` | App de OneSignal | No | *(sin definir)* | Igual: `not_configured` | `notifications/push.py:45` |

### B.3 Tabla de variables — web (`apps/web`)

Las `NEXT_PUBLIC_*` se **inyectan en tiempo de build** (no de ejecución): hay que
reconstruir la imagen para cambiarlas.

| Variable | Para qué | Obligatoria | Defecto | Qué se rompe si falta | Fuente |
| --- | --- | --- | --- | --- | --- |
| `NEXT_PUBLIC_ARGUS_API_URL` | URL base de la API | **Sí (build de producción)** | En dev: `http://localhost:8000`. En producción: **`null`** | El cliente **no** cae a localhost: pinta un aviso de configuración visible (`API_CONFIG_ERROR`) y no hay backend alcanzable | `lib/config.ts:16-36` |
| `NEXT_PUBLIC_ARGUS_TIMEOUT_MS` | Timeout de fetch (ms, 1000–120000) | No | `12000` | Se usa el defecto; fuera de rango también | `lib/config.ts:43` |
| `ARGUS_API_ORIGIN` | Destino del rewrite `/api/*` → API (modo servidor) | No | `http://127.0.0.1:8000` | En modo servidor, `/api/*` apunta a localhost del contenedor web | `next.config.js:11` |
| `NEXT_STATIC_EXPORT` | `1` ⇒ `output: "export"` y sin rewrites | No | *(sin definir)* | Modo servidor normal | `next.config.js:5,9` |
| `NODE_ENV` | `production` en la imagen final; lo lee `config.ts` para decidir si puede caer a localhost | No | lo fija el Dockerfile | Si no es `production`, un build sin `NEXT_PUBLIC_ARGUS_API_URL` cae silenciosamente a localhost | `lib/config.ts:18`, `apps/web/Dockerfile:16` |

### B.4 Diferencias entre lo que declara `render.yaml` y lo que lee el código

| Variable en `render.yaml` | Situación |
| --- | --- |
| `DATABASE_URL`, `REDIS_URL`, `SUPABASE_JWT_SECRET`, `RESEND_API_KEY`, `RESEND_FROM`, `ONESIGNAL_APP_ID`, `ONESIGNAL_API_KEY`, `TRUSTED_PROXIES` | `sync: false` — hay que rellenarlas en el panel de Render; el código las lee |
| `AUTH_DISABLED="false"`, `ARGUS_ENV="production"`, `CORS_ORIGINS=...`, `MIGRATIONS_FATAL="1"`, `ARGUS_DB_CONNECT_TIMEOUT="5"`, `FORWARDED_ALLOW_IPS="127.0.0.1"` | valores literales en el blueprint |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY` | **declaradas y no consumidas** por ningún código del repositorio (**NO CONFIRMADO**) |

### B.5 Códigos de salida y diagnóstico

| Síntoma | Causa probable | Comprobación |
| --- | --- | --- |
| El contenedor arranca y muere al instante con `FATAL: AUTH_DISABLED=true but no explicit local/dev opt-in` | `AUTH_DISABLED=true` sin `ARGUS_ENV` de dev ni `ARGUS_ALLOW_AUTH_DISABLED` | `middleware.assert_auth_configuration` |
| El contenedor arranca y muere con `CORS_ORIGINS must be set to an explicit origin allowlist` | `CORS_ORIGINS` vacío o `*` fuera de dev | `middleware.assert_cors_configuration` |
| El release falla en el paso de migraciones | `MIGRATIONS_FATAL=1` con migración fallida o **DRIFT** de checksum | `python -m app.migrate` en local; el log nombra el archivo |
| La API responde `503 AUTH_NOT_CONFIGURED` | Falta `SUPABASE_JWT_SECRET` | `/health` y el log (`SUPABASE_JWT_SECRET is not set — failing closed`) |
| `/health` devuelve `status: "degraded"` y `data_layer: "postgres (UNREACHABLE)"` | `DATABASE_URL` definido pero la base no responde | `db_configured: true`, `db_reachable: false` |
| Todos los clientes comparten límite de rate | `TRUSTED_PROXIES` vacío detrás de un proxy | el log de `client_identity` / el bucket `ip:` |
| Las predicciones no quedan guardadas | Sin `DATABASE_URL` (stub) o pool caído | log `DATABASE_URL is not set — running WITHOUT Postgres` |

---

## Fase C — Verificación después del despliegue

```bash
BASE=https://<servicio>

# 1. Liveness + estado honesto de la capa de datos (siempre 200)
curl -s $BASE/health

# 2. Contrato OpenAPI publicado por la app (debe coincidir con docs/API_SPEC.md)
curl -s $BASE/openapi.json | python -c "import json,sys; d=json.load(sys.stdin); print(len(d['paths']), 'rutas')"

# 3. Envelope de una ruta pública de datos
curl -s -H "Authorization: Bearer $TOKEN" $BASE/api/v1/assets | head -c 400

# 4. Auth fallando cerrado (sin token debe ser 401 con sobre de error)
curl -s -i $BASE/api/v1/assets | head -n 20

# 5. Rate limit (cabeceras presentes)
curl -s -D - -o /dev/null $BASE/api/v1/assets | grep -i ratelimit

# 6. 404 con código nombrado (nunca un 4xx genérico)
curl -s $BASE/api/v1/no-existe
```

Criterios de aceptación:

- `/health` responde **200** y su `data_layer` coincide con lo esperado
  (`postgres` si hay base; si dice `stub`, la base no está conectada).
- `/openapi.json` publica **27 rutas / 29 operaciones** (mismo número que
  `docs/API_SPEC.md`; si no coincide, el documento está desactualizado →
  `python repo/tools/gen_api_spec.py`).
- Sin token, una ruta protegida devuelve **401** con
  `{"error":{"code":"UNAUTHORIZED"}}`.
- Un `X-Request-ID` inválido se sustituye (nunca se refleja tal cual).
- Las respuestas llevan `X-Request-ID`, `X-Process-Time` y, cuando aplica,
  `X-RateLimit-Limit` / `X-RateLimit-Remaining`.

---

## Anexo — Lista completa de variables confirmadas

Orden alfabético; todas verificadas leyendo el código, con su archivo:

```
ARGUS_ALLOW_AUTH_DISABLED   services/ml/app/authz.py:77
ARGUS_ALERTS_DB             services/ml/app/routers/alerts.py:117
ARGUS_API_ORIGIN            apps/web/next.config.js:11
ARGUS_DB_CONNECT_TIMEOUT    services/ml/app/migrate.py:103
ARGUS_DB_DSN                services/ml/app/db.py:39
ARGUS_ENV                   services/ml/app/authz.py:75
ARGUS_HEALTH_DB_TIMEOUT     services/ml/app/main.py:151
ARGUS_MIGRATE_OPTIONAL      services/ml/app/migrate.py:94
ARGUS_RESEARCH_DB           services/ml/app/research_store.py:127
ARGUS_RETRY_SKIPPED         services/ml/app/migrate.py:209
AUTH_DISABLED               services/ml/app/authz.py:70
CORS_ALLOW_HEADERS          services/ml/app/middleware.py:442
CORS_ALLOW_METHODS          services/ml/app/middleware.py:440
CORS_ORIGINS                services/ml/app/middleware.py:425,458
DATABASE_URL                services/ml/app/migrate.py:110, db.py:39
FORWARDED_ALLOW_IPS         services/ml/start.sh:31
MIGRATIONS_FATAL            services/ml/app/migrate.py:96
NEXT_PUBLIC_ARGUS_API_URL   apps/web/lib/config.ts:16
NEXT_PUBLIC_ARGUS_TIMEOUT_MS apps/web/lib/config.ts:43
NEXT_STATIC_EXPORT          apps/web/next.config.js:5,9
NODE_ENV                    apps/web/lib/config.ts:18
ONESIGNAL_API_KEY           services/ml/app/notifications/push.py:42
ONESIGNAL_APP_ID            services/ml/app/notifications/push.py:45
PORT                        services/ml/start.sh:29
PRED_CACHE_TTL              services/ml/app/cache.py:31
PRED_MAX_WORKERS            services/ml/app/routers/predictions.py:265
RATE_LIMIT_ANON_PER_MIN     services/ml/app/middleware.py:505
RATE_LIMIT_AUTH_PER_MIN     services/ml/app/middleware.py:506
RATE_LIMIT_HEAVY_PER_MIN    services/ml/app/middleware.py:507
RATE_LIMIT_REDIS_COOLDOWN_S services/ml/app/rate_limit.py:66
REDIS_URL                   services/ml/app/middleware.py:516, rate_limit.py:179, cache.py:26
RESEND_API_KEY              services/ml/app/notifications/email.py:41
RESEND_FROM                 services/ml/app/notifications/email.py:45
SUPABASE_JWT_AUD            services/ml/app/authz.py:96
SUPABASE_JWT_ISS            services/ml/app/authz.py:101
SUPABASE_JWT_SECRET         services/ml/app/authz.py:88
TRUSTED_PROXIES             services/ml/app/middleware.py:184
```

**Declaradas sin consumidor (NO CONFIRMADO):** `SUPABASE_URL`,
`SUPABASE_ANON_KEY` (sólo en `render.yaml` y `.env.example`).

No se documenta ninguna variable más: no existe en el código.
