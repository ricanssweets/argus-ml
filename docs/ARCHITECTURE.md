# ARGUS — ARCHITECTURE (el sistema tal como está construido)

> **Documento escrito a mano a partir del código real.** Cada afirmación cita el
> archivo que la sostiene. No hay especificación previa en el repositorio: hasta
> ahora `docs/` no existía y las referencias a `docs/ARCHITECTURE.md` repartidas
> por el código apuntaban a un archivo inexistente. Este documento describe **lo
> que el código hace hoy**, no lo que se pretendía hacer; cuando algo está
> declarado pero no implementado, se dice explícitamente.
>
> Documentos hermanos: [API_SPEC.md](./API_SPEC.md) (generado),
> [DATABASE_SCHEMA.md](./DATABASE_SCHEMA.md),
> [DEPLOYMENT_CHECKLIST.md](./DEPLOYMENT_CHECKLIST.md).

---

## 1. Vista general: tres artefactos desplegables

| # | Artefacto | Ruta | Tecnología | Cómo se empaqueta | Punto de entrada |
| --- | --- | --- | --- | --- | --- |
| 1 | API / ML service | [`services/ml`](../services/ml) | FastAPI (Python 3.12) | [`services/ml/Dockerfile`](../services/ml/Dockerfile) (`python:3.12-slim`) | [`services/ml/start.sh`](../services/ml/start.sh) → `uvicorn app.main:app`, puerto `${PORT:-8000}` |
| 2 | Web / dashboard | [`apps/web`](../apps/web) | Next.js 14 App Router + TypeScript + Tailwind + Recharts | [`apps/web/Dockerfile`](../apps/web/Dockerfile) (`node:24-alpine`, multi-stage) | `npm start`, puerto 3000 |
| 3 | Librería cuantitativa | [`packages/quant`](../packages/quant) | Paquete Python `argus-quant` 0.1.0 (`pyproject.toml`, `>=3.12`) | `pip install` (se instala **dentro** de la imagen del servicio ML) | se importa como `argus_quant` |

Notas verificadas:

- Sólo el servicio ML está declarado como blueprint en
  [`render.yaml`](../render.yaml) (`type: web`, `runtime: docker`,
  `healthCheckPath: /health`, `preDeployCommand: python -m app.migrate`).
  El despliegue de `apps/web` **no está declarado** en el repositorio: existe su
  Dockerfile, pero el mecanismo real de despliegue no es confirmable desde el
  código.
- El servicio ML **no** contiene lógica cuantitativa propia: importa
  `argus_quant` a través de [`app/quant_compat.py`](../services/ml/app/quant_compat.py),
  que (a) intenta el import normal, (b) cae a `sys.path` con
  `~/workspace/argus/packages/quant`, y (c) si tampoco está, degrada a modo
  demo con `data_status=UNCONFIRMED` y lo dice en el log. `QUANT_AVAILABLE`
  se publica en `/health`.
- El Dockerfile del servicio ML instala el paquete compartido
  (`pip install /tmp/argus-quant`) precisamente porque el pipeline de 8 motores
  lo necesita; sin él el servicio no inventa una copia, se degrada.

---

## 2. Flujo de una petición y orden del middleware

El orden está cableado en [`app/main.py`](../services/ml/app/main.py)
(`add_middleware` inserta por fuera, y los `add_middleware` van de dentro a
fuera). Orden de ejecución real, exterior → interior:

```
cliente
  │
  ├─ CORSMiddleware ............ allowlist explícita; preflight antes de auth
  ├─ RequestIDMiddleware ....... sanea / genera X-Request-ID, añade X-Process-Time
  ├─ AuditLogMiddleware ........ 1 línea JSONL a `argus.audit` + INSERT best-effort en system_logs
  ├─ RateLimitMiddleware ....... bucket anon/auth/heavy; Redis o memoria
  ├─ AuthMiddleware ............ JWT HS256 de Supabase (o bypass dev explícito)
  └─ router (/api/v1/...) ...... sobre {data, meta}; ownership vía app.authz
```

Invariantes que el código realmente aplica:

| Invariante | Dónde | Efecto observable |
| --- | --- | --- |
| Falla cerrado en `AUTH_DISABLED` sin opt-in dev | `middleware.assert_auth_configuration()` llamado desde el **lifespan** de `main.py` | el proceso no arranca (`RuntimeError`) en vez de servir tráfico anónimo |
| Falla cerrado en CORS mal configurado fuera de dev | `middleware.assert_cors_configuration()` en el mismo lifespan | no arranca si `CORS_ORIGINS` está vacío o es `*` |
| El bucket se deriva de identidad **verificada** | `app/authz.py::token_identity` | un `Authorization` que no verifica cae en `anon` y luego recibe 401 |
| `X-Forwarded-For` sólo desde proxies de confianza | `middleware.client_identity` + `TRUSTED_PROXIES` | con la variable vacía, el header se ignora |
| La escritura de auditoría no bloquea el event loop | `AuditLogMiddleware` + `anyio.to_thread.run_sync` | un `system_logs` caído sólo produce un warning |
| Rate limit nunca convierte un fallo de backend en 500 | `app/rate_limit.py` (contrato «never raises») + `RateLimitMiddleware.dispatch` | degrada a memoria, o falla cerrado con 503 `RATE_LIMIT_UNAVAILABLE` |
| Errores siempre con código estable | `main._HTTP_ERROR_CODES`, `_error()` | `{error{code,message,details}}`; ningún 4xx/5xx queda genérico |

Tratamiento de errores: `StarletteHTTPException` → código nombrado
(`_HTTP_ERROR_CODES`, y `HTTP_<estado>` como último recurso);
`RequestValidationError` → `VALIDATION_ERROR` (422) con `details.errors`;
cualquier otra excepción → `INTERNAL_ERROR` (500) con `log.exception`.

Ciclo de vida: el lifespan ejecuta los dos guards de configuración al arrancar y
`db.close_pool()` al apagar. Importar `app.main` **no** ejecuta los guards, a
propósito, para que tests/linters/generadores de documentación puedan importar
la app con cualquier entorno (esto es lo que hace posible
[`tools/gen_api_spec.py`](../tools/gen_api_spec.py)).

Detalle completo de auth, buckets y cabeceras: [API_SPEC.md §Autenticación y
rate limit](./API_SPEC.md#autenticación-y-rate-limit-tal-como-están-implementados).

---

## 3. Capas de datos

El servicio tiene **cuatro** capas de datos que se usan en este orden de
preferencia y que degradan sin romper el servicio.

### 3.1 Postgres (producción) — `app/db.py`

- DSN: `DATABASE_URL`, con respaldo heredado `ARGUS_DB_DSN`
  (`db.database_url()`).
- Pool: `psycopg_pool.ConnectionPool(min_size=1, max_size=10, timeout=5,
  kwargs={"connect_timeout": 5})`, uno por proceso y perezoso (`get_pool()`).
- **Degradación agraciada, por contrato**: `get_pool()` devuelve `None` ante
  cualquier problema (incluido que falte el extra `psycopg_pool`), `fetchall()`
  devuelve `[]`, `fetchone()` `None`, `execute()` `0`. Nunca lanza por base de
  datos; sólo por errores de programación. Los avisos son ruidosos y únicos
  (`_warned_unset`, `_warned_unreachable`).
- Los `?` de las consultas se traducen a `%s` (`_to_pg_placeholders`).
- Escritura de predicciones **append-only**:
  `insert_prediction()` usa `INSERT ... ON CONFLICT (as_of, asset_id, horizon,
  model_version) DO NOTHING RETURNING id`; un conflicto no es un error, es
  «ya estaba bloqueado».
- Tablas espejo en el esquema `svc` (`MIRROR_DDL`: `svc.research_*`,
  `svc.alert_*`) para que alertas e investigación funcionen igual contra
  Postgres que contra SQLite. `ensure_mirror()` omite `CREATE SCHEMA` si el
  esquema ya existe, porque el rol de aplicación no tiene privilegio de
  creación.

### 3.2 Migraciones — `app/migrate.py`

- Libro mayor `schema_migrations(filename PK, checksum, status, detail,
  applied_at)`; se crea con `CREATE TABLE IF NOT EXISTS` aunque la migración 009
  también lo defina.
- Cada archivo se aplica en una transacción **junto con** su fila de ledger: no
  hay estados a medias.
- Checksum sha256: si el archivo cambió después de aplicarse, se reporta
  **DRIFT** y **no se reaplica**; si un archivo falla, no se registra y se
  reintenta en la siguiente ejecución.
- `007_roles.sql` está en `SKIP_FILES`: requiere `CREATE ROLE` (superusuario); si
  falla se registra `status='skipped'` con el motivo en vez de reintentarse
  eternamente (`ARGUS_RETRY_SKIPPED=1` fuerza el reintento).
- **Nunca bloquea el arranque por defecto**: `fatal_mode()` sólo devuelve `True`
  con `MIGRATIONS_FATAL` explícito y sin `ARGUS_MIGRATE_OPTIONAL=1`.
- Conectividad: `probe_database()` (usado por `/health`) abre su propia conexión
  con `connect_timeout` acotado y no lanza nunca.

### 3.3 SQLite (sidecars locales)

| Archivo | Módulo | Tablas | Override |
| --- | --- | --- | --- |
| `services/ml/data/alerts.db` | [`app/routers/alerts.py`](../services/ml/app/routers/alerts.py) | `alert_rules`, `alerts` | `ARGUS_ALERTS_DB` |
| `services/ml/data/research_store.db` | [`app/research_store.py`](../services/ml/app/research_store.py) | `assets`, `predictions`, `prediction_outcomes`, `research_backtests`, `model_metrics_daily` | `ARGUS_RESEARCH_DB` |

Las tablas SQLite **replican los nombres y la forma** de las tablas Postgres;
cuando `DATABASE_URL` está definido, ambos módulos leen y escriben las tablas
espejo `svc.*` en lugar del archivo local (en `alerts.py` mediante la reescritura
de `_svc()`). Las filas sembradas del research store llevan
`data_status='SYNTHETIC_FIXTURE'` y la API lo propaga; nunca se presentan como
datos de mercado.

### 3.4 Redis (opcional) — caché y rate limit

- Caché de predicciones: [`app/cache.py`](../services/ml/app/cache.py), clave
  `pred:{ticker}:{horizon}:{asof_date}`, TTL `PRED_CACHE_TTL` (300 s por
  defecto), backoff de 60 s tras un fallo y jamás lanza (un fallo de caché es un
  *miss*).
- Rate limit: [`app/rate_limit.py`](../services/ml/app/rate_limit.py), script
  Lua atómico sobre un ZSET por `(bucket, identidad)`; recarga el script ante
  `NOSCRIPT`; ante cualquier otro error degrada a ventana en proceso durante
  `RATE_LIMIT_REDIS_COOLDOWN_S` (10 s) con un log único por episodio.
- Sin `REDIS_URL` no hay caché y el limitador es **por réplica** (se avisa en el
  log). Esto es correcto en una réplica y mentira operativa en varias: por eso
  está documentado.

### 3.5 Capa stub (sin base de datos)

[`app/data_stub.py`](../services/ml/app/data_stub.py) mantiene un universo
determinista de activos y series sintéticas para que la API arranque sin
Postgres. Todo lo que produce lleva `data_status="UNCONFIRMED"` y la etiqueta
`DEMO/STUB — synthetic data, not real market data`. `/health` lo declara como
`data_layer: "stub (UNCONFIRMED demo)"`.

Precios reales: [`app/prices.py`](../services/ml/app/prices.py) descarga OHLCV
diario de Yahoo (sin clave), con caché en disco en
`~/workspace/argus/data/raw/<TICKER>.csv` y respaldo en las instantáneas
incluidas en la imagen (`services/ml/data/baked/*.csv`). Los precios **no** se
sintetizan aquí: si falla, lanza `PriceFetchError` y el router lo traduce a un
sobre de error.

---

## 4. Registro de modelos y calibración

Todo vive en [`services/ml/app/registry/`](../services/ml/app/registry):

| Archivo | Contenido | Lo lee |
| --- | --- | --- |
| `ARGUS-EQ-1.0.json` | pesos **priors** de la versión 1.0 (historia intacta) | [`app/ensemble.py`](../services/ml/app/ensemble.py) — `VERSION = "ARGUS-EQ-1.0"`, ruta `registry/{VERSION}.json` |
| `ARGUS-EQ-1.1.json` | pesos por régimen de la 1.1, `engine_availability`, bloque OOF global y por régimen | [`app/predict.py`](../services/ml/app/predict.py) (`_load_registry`) y [`app/observatory.py`](../services/ml/app/observatory.py) (`read_registry`, `registry_oof`) |
| `calibration_ARGUS-EQ-1.1.json` | mapa heredado, **sólo horizonte 20** | `predict._load_registry` / `predict._calibration_for` (fallback) y `observatory._calibration_doc_path` |
| `calibration/calibration_ARGUS-EQ-1.1_h{1,3,5,10,20,63,126,252}.json` | un mapa isotónico por horizonte (8 archivos) | `predict._calibration_for(h)` y `observatory._calibration_doc_path` |

Resolución de rutas: `REPO = dirname^4(abspath(__file__))` y
`REGISTRY_DIR = <repo>/services/ml/app/registry`, repetido en `predict.py:51-53`
y `observatory.py:51-54`. El Dockerfile conserva el layout del monorepo
(`WORKDIR /srv/argus/services/ml`) exactamente para que esa resolución siga
funcionando en la imagen.

Semántica del pipeline en vivo (`predict.build_live_prediction`):

1. La probabilidad calibrada se calcula **sólo** con los motores A/B/H
   (`Technical`, `Factors`, `CrossAsset`), que son los que participaron en el
   aprendizaje OOF de la 1.1: `p_raw = Σ w_i · (s_i + 1) / 2`.
2. `p_raw` pasa por el mapa isotónico del horizonte y del régimen.
3. Los motores C/D/F/G **no** entran en `p_positive`: se publican como señales
   superpuestas y ajustan el `composite_score` con pesos *priors*
   (0.10/0.10/0.05/0.05). El propio módulo documenta que meterlos en la
   probabilidad sin validación OOF violaría las reglas de honestidad.
4. Si el régimen es delgado (menos de 50 muestras OOF), la entrada del mapa trae
   `calibrated: false` y el resultado se devuelve **sin calibrar**, con penalización
   de confianza y `calibrated: false` en la respuesta.
5. `engine_availability` de la 1.1 marca `false` para Fundamentals/Macro/Regime/
   Options/Sentiment: esos motores reciben peso 0, nunca se fabrican.

El observatorio (`observatory.py`) resuelve métricas en tres saltos, cada uno
nombrado en el campo `source` de la respuesta: (1) Postgres
(`model_metrics_daily`, `prediction_outcomes`+`predictions`, `calibration_maps`,
`degradation_flags`), (2) los JSON del registro, (3) nada — `None` +
`data_status="MISSING"` + nota de qué fuentes se consultaron. Es la respuesta
directa a que antes se generaran Brier/ROC-AUC «inventados» con un PRNG.

Los mapas del registro guardan `n` por *slice* de régimen, no por nudo: por eso
`n` por nudo es `None` con `n_status="MISSING"` (documentado en la cabecera del
módulo) en lugar de inventarse.

Promoción de modelos: **no hay promoción automática en el código**. Los JSON del
registro declaran una política (`promotion_policy`: challenger sólo tras mejora
out-of-sample significativa) y la migración 008 menciona un fichero
`data/promotion_decisions.jsonl`; no existe código que lo escriba, así que ese
punto queda **no confirmado / no implementado**. Lo único real hoy es que
`model_versions.status` no se modifica desde la aplicación.

---

## 5. El principio de degradación honesta

Regla que gobierna el repositorio y que sí está implementada: **lo que falta se
reporta, no se rellena**. Se ve en cinco mecanismos concretos.

| Mecanismo | Implementación | Qué evita |
| --- | --- | --- |
| `data_status` por registro y por sobre | `app/schemas.py::DataStatus` (6 valores) + `routers/__init__.py::aggregate_status` | que un sobre parezca mejor que sus filas |
| `/health` siempre 200 y honesto | `main.health()` + `_db_reachable()` con timeout acotado | que un `restart-loop` por base de datos caída tumbe el servicio, y que se declare `postgres` con Postgres caído |
| Observatorio sin números sintéticos | `observatory.py` (fuente nombrada o `MISSING`) | métricas inventadas |
| Entrega push/email explícitamente no configurada | `app/delivery.py` + `app/notifications/*` → `{"status": "not_configured", "reason": ...}` nombrando la variable que falta | un envío simulado |
| Servicio sin la librería cuantitativa | `quant_compat` degrada y `alerts.evaluate` responde **503** en vez de duplicar la lógica | lógica cuantitativa duplicada y divergente |

Detalles de `/health`: `data_layer` puede ser `"stub (UNCONFIRMED demo)"`,
`"postgres"` o `"postgres (UNREACHABLE)"`; `status` es `"ok"` o `"degraded"`.
El sondeo usa `migrate.probe_database(timeout=ARGUS_HEALTH_DB_TIMEOUT)`, es
decir una conexión propia con timeout, **no** el pool (que puede tardar el
timeout completo del pool).

---

## 6. Las «locked laws» del paquete cuantitativo y su estado real

`packages/quant/argus_quant/__init__.py` declara cuatro leyes. Estado verificado
contra el código (no contra la intención):

| Ley declarada | Dónde se declara | Dónde se aplica de verdad | Estado |
| --- | --- | --- | --- |
| **1. Sin fuga de futuro**: todo indicador/feature en la barra `t` usa barras `<= t` | `__init__.py:6-7` | `validation.py::assert_no_leakage` (lanza `AssertionError` con las filas culpables), `validation.walk_forward_splits` / `purged_kfold_splits`, `features.py` (`MIN_HISTORY_BARS` y `ValueError` si no hay barras `<= as_of`), `engines.py:392` (`no bars at or before as_of`), `indicators.py` (sólo exige OHLCV + `DatetimeIndex`) | **Parcial.** La comprobación existe y es executable, pero `assert_no_leakage` **no se invoca en ningún sitio** del servicio ML (sólo se define). `ensemble.py:288,295` usa `walk_forward_splits`. El README de `packages/quant` afirma que la ley «está impuesta por el test `tests/test_no_leakage.py`»: **ese archivo no existe** (no hay `tests/` en el repositorio). |
| **2. Los calibradores se ajustan SÓLO con predicciones out-of-fold** | `__init__.py:8`, `calibration.py:3,48,113` | `calibration.py::_check_oof` (longitudes, `n >= MIN_SAMPLES_PER_BUCKET`, probabilidades en `[0,1]`, etiquetas binarias) + la API recibe parámetros llamados `oof_predictions`/`oof_labels` | **Parcial.** La API hace imposible pasar un array de entrenamiento *por nombre*, y valida forma/rango, pero **no puede comprobar** que esos valores sean realmente OOF: es un contrato de uso. |
| **3. Un bucket de calibración necesita `MIN_SAMPLES_PER_BUCKET = 20`** | `__init__.py:9`, `metrics.py:15` | `metrics.py::calibration_table` (fusiona buckets < 20 con vecinos y marca `insufficient`), `metrics.py:126,151`, `calibration.py:34-37` (lanza `ValueError`), y **en la base de datos**: `calibration_maps.min_samples INT NOT NULL CHECK (min_samples >= 20)` (migración 004) | **Sí, la más fuerte de las cuatro.** Se aplica en código y en el esquema; `observatory.py` propaga `MIN_SAMPLES = 20`. |
| **4. Los datos que faltan se reportan (`data_status`), nunca se fabrican** | `__init__.py:10` | `schemas.py::DataStatus` + `routers/__init__.py::aggregate_status`; `observatory.py` (tres fuentes o `MISSING`); `predict.py::STATUS_RANK`; `data_stub` etiquetado; `delivery.py` | **Sí, por convención aplicada en todo el código de servicio** (no hay aserción automática que lo garantice). Es el principio con más superficie implementada. |

Conclusión honesta: de las cuatro leyes, una (mínimo de muestras) está impuesta
también a nivel de base de datos; dos están implementadas como código ejecutable
pero **no invocadas** en el camino de producción; y la cuarta es una convención
respetada por el código de servicio, sin test que la vigile. El repositorio no
tiene tests ni CI: no hay ninguna barrera automática que impida una regresión.

---

## 7. Lo que NO existe (para no volver a citarlo)

| Citado en el código | Estado real |
| --- | --- |
| `docs/ARCHITECTURE.md` (este documento), `docs/API_SPEC.md` (generado), `docs/DATABASE_SCHEMA.md`, `docs/DEPLOYMENT_CHECKLIST.md` | existían **sólo** como citas; se crean aquí |
| `docs/MODEL_EVALUATION.md`, `docs/PERFORMANCE.md` | **no existen** y no se crean aquí: no hay código que los respalde (la clave de caché `pred:*` y su TTL sí están en `app/cache.py`) |
| `tests/` (cualquier test), workflows de CI | **no existen** en el repositorio |
| Cola de trabajos / workers | no hay: `POST /api/v1/predictions` responde 202 y materializa el resultado en el acto (`routers/predictions.py`), el `job_id` es informativo |
| Promoción automática de modelos | no hay (ver §4) |
| `~/workspace/argus/...` | rutas de un layout de desarrollo que **no** es este monorepo; sobreviven como fallback en `quant_compat.py` (quant), `prices.py` (`data/raw`) y en la cita de `apps/web/README.md` |

---

## 8. Mapa de archivos del servicio ML

```
services/ml/
  start.sh                  migra (no bloqueante) y exec uvicorn
  Dockerfile                python:3.12-slim + argus-quant + db/ (migraciones y seeds)
  app/
    main.py                 app FastAPI, orden de middleware, handlers de error, /health
    middleware.py           RequestID / AuditLog / RateLimit / Auth + guards de arranque + CORS
    authz.py                verificación JWT HS256, identidad verificada, ownership (401/403/404)
    rate_limit.py           ventana deslizante Redis (Lua) + fallback en proceso
    cache.py                caché Redis opcional de predicciones
    db.py                   pool psycopg, helpers, append-only predictions, espejo svc.*
    migrate.py              ledger con checksums, no bloqueante, probe para /health
    data_stub.py            universo determinista para arrancar sin base de datos
    prices.py               OHLCV real (Yahoo) + caché en disco + instantáneas baked
    predict.py              pipeline en vivo de 8 motores + calibración
    ensemble.py             ensemble versionado ARGUS-EQ-1.0 (sklearn, priors)
    observatory.py          métricas reales o MISSING (nunca inventadas)
    research_planner.py     respuestas del asistente de investigación con citas
    research_store.py       sidecar SQLite de investigación
    quant_compat.py         import defensivo de argus_quant
    delivery.py             push/email real o "not_configured"
    notifications/          clientes OneSignal (push) y Resend (email)
    schemas.py              modelos Pydantic (DataStatus, Envelope, ErrorEnvelope…)
    registry/               pesos por régimen + mapas de calibración
    routers/                assets, predictions, signals, etfs, portfolio,
                            backtests, models, research, alerts
```

Documentación de la base de datos: [DATABASE_SCHEMA.md](./DATABASE_SCHEMA.md).
Variables de entorno y despliegue: [DEPLOYMENT_CHECKLIST.md](./DEPLOYMENT_CHECKLIST.md).
