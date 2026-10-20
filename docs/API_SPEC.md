# ARGUS ML — API_SPEC (GENERADO)

> **Archivo generado. No editar a mano.**
>
> Regenerar con:
>
> ```bash
> python repo/tools/gen_api_spec.py
> ```
>
> Generador: [`tools/gen_api_spec.py`](../tools/gen_api_spec.py). Fuente: `app.openapi()` de `services/ml/app/main.py` (esquema OpenAPI que la propia app publica en `/openapi.json`), más `services/ml/app/middleware.py` para autenticación y rate limit, y `services/ml/app/main.py::_HTTP_ERROR_CODES` para los códigos de error.
>
> El documento no lleva marca de tiempo a propósito: regenerarlo sobre el mismo código produce un diff vacío.

## Identidad del artefacto

| Campo OpenAPI | Valor |
| --- | --- |
| `openapi` | `3.1.0` |
| `info.title` | ARGUS ML Service |
| `info.version` | `0.1.0` |
| `info.description` | Institutional AI stock & ETF prediction platform — ML service. Ensemble predictor with Postgres persistence and calibration maps. |
| Rutas en `paths` | 27 |
| Operaciones (método + ruta) | 29 |
| Esquemas en `components.schemas` | 11 |

Entorno con el que se generó este archivo (no altera el esquema, sólo lo hace reproducible):

- `ARGUS_ENV=development`
- `AUTH_DISABLED=true`
- `CORS_ORIGINS=http://localhost:3000`
- `DATABASE_URL` y `REDIS_URL`: retiradas por el generador (capa de datos stub, limitador en proceso).

Detección de seguridad en el esquema: esquemas `securitySchemes` declarados: ninguno; clave `security` en la raíz: no.

## Sobre de respuesta universal (`data` + `meta`)

Toda respuesta correcta se construye con `app/routers/__init__.py::env()`:

```json
{
  "data": <cuerpo específico de la ruta>,
  "meta": {
    "as_of": "2026-10-06T17:59:56Z",
    "data_status": "UNCONFIRMED",
    "request_id": "3f9c1e0d8a7b4c2f9e1a6d0b5c4e2f18"
  }
}
```

| Clave | Tipo | Obligatoria | Origen |
| --- | --- | --- | --- |
| `data` | cualquiera | sí | handler de la ruta |
| `meta.as_of` | `string` (ISO-8601 UTC) | sí | `data_stub.utcnow_iso()` o el `as_of` real del dato |
| `meta.data_status` | `enum` | sí | el handler, o `aggregate_status()` (peor estado de los ítems) |
| `meta.request_id` | `string` | sí | `request.state.request_id` (ver `X-Request-ID` en §Middleware) |
| `meta.filters` | `object` | no | sólo `GET /api/v1/predictions` (`routers/predictions.py`) |
| `meta.notes` | `array[string]` | no | sólo `GET /api/v1/scanner` cuando el pipeline no produce filas |

Valores admitidos de `data_status` (`app/schemas.py::DataStatus`, extraídos del `Literal`):

- `CONFIRMED`
- `LIKELY`
- `UNCONFIRMED`
- `CONFLICTING`
- `MISSING`
- `SYNTHETIC_FIXTURE`

> **Honestidad del contrato:** este build **no** emite las claves `meta.demo` ni `meta.error`. No existen en el código (`routers/__init__.py::env()` sólo escribe `as_of`, `data_status`, `request_id` y, opcionalmente, `filters` / `notes`). Si algún consumidor las espera, no están implementadas: no se documentan como si lo estuvieran.

> `meta.data_status` nunca es mejor que los registros que transporta: `aggregate_status()` (`app/routers/__init__.py`) devuelve el estado más pesimista (`CONFIRMED < LIKELY < UNCONFIRMED = SYNTHETIC_FIXTURE < CONFLICTING < MISSING`).

## Sobre de error

Todo error se serializa como (`app/main.py::_error`, idéntico en `app/middleware.py::_error`):

```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "Rate limit exceeded: 60 requests/min (anon).",
    "details": null
  }
}
```

| Clave | Tipo | Obligatoria |
| --- | --- | --- |
| `error.code` | `string` | sí — estable, es lo que los clientes deben comparar |
| `error.message` | `string` | sí — prosa, no usar para lógica |
| `error.details` | `object` / `null` | sí (nullable); sólo `VALIDATION_ERROR` lo rellena |

### Códigos implementados en `app/main.py::_HTTP_ERROR_CODES`

| Estado HTTP | `error.code` |
| --- | --- |
| 400 | `BAD_REQUEST` |
| 401 | `UNAUTHORIZED` |
| 403 | `FORBIDDEN` |
| 404 | `NOT_FOUND` |
| 405 | `METHOD_NOT_ALLOWED` |
| 409 | `CONFLICT` |
| 413 | `PAYLOAD_TOO_LARGE` |
| 415 | `UNSUPPORTED_MEDIA_TYPE` |
| 422 | `VALIDATION_ERROR` |
| 429 | `RATE_LIMITED` |
| 500 | `INTERNAL_ERROR` |
| 503 | `SERVICE_UNAVAILABLE` |
| 504 | `GATEWAY_TIMEOUT` |

Un estado no incluido en esa tabla cae al código `HTTP_<estado>` (`app/main.py`: `f"HTTP_{exc.status_code}"`), nunca a un genérico. `RequestValidationError` produce `VALIDATION_ERROR` con `details.errors`; cualquier excepción no controlada produce `INTERNAL_ERROR`.

### Códigos emitidos por el middleware (fuera de `_HTTP_ERROR_CODES`)

| Estado HTTP | `error.code` | Emitido por |
| --- | --- | --- |
| 401 | `UNAUTHORIZED` | `AuthMiddleware` (token ausente o inválido) |
| 503 | `AUTH_NOT_CONFIGURED` | `AuthMiddleware` (`SUPABASE_JWT_SECRET` ausente: falla cerrado) |
| 429 | `RATE_LIMITED` | `RateLimitMiddleware` (+ cabeceras `Retry-After`, `X-RateLimit-*`) |
| 503 | `RATE_LIMIT_UNAVAILABLE` | `RateLimitMiddleware` (el backend del limitador rompió su contrato: falla cerrado) |

Estos cuatro códigos están en `services/ml/app/middleware.py` (`_error(...)`), no en `_HTTP_ERROR_CODES`: se listan porque son parte real del contrato observable y se localizaron leyendo el código.

## Autenticación y rate limit tal como están implementados

Ambos viven en el **middleware**, no en las rutas: el esquema OpenAPI **no** declara mecanismos de seguridad (`securitySchemes` / `security`), así que ninguno de los endpoints aparece como protegido en el JSON OpenAPI. La protección es real, pero externa al esquema.

### Rutas públicas (`app/middleware.py::PUBLIC_PATHS`)

```python
PUBLIC_PATHS = {"/docs", "/health", "/openapi.json", "/redoc"}
```

| Ruta | Exenta de auth | Exenta de rate limit | Exenta de audit log |
| --- | --- | --- | --- |
| `/docs` | sí | sí | no |
| `/health` | sí | sí | sí |
| `/openapi.json` | sí | sí | no |
| `/redoc` | sí | sí | no |

Además, `AuthMiddleware` y `RateLimitMiddleware` eximen cualquier ruta que empiece por `/docs` (`path.startswith("/docs")`); `AuditLogMiddleware` sólo excluye la ruta exacta `/health`.

El resto de rutas exige `Authorization: Bearer <JWT>` HS256 de Supabase. `AUTH_DISABLED=true` se rechaza en el arranque salvo opt-in explícito de desarrollo (`ARGUS_ENV` en `{development, dev, local, test, testing}` o `ARGUS_ALLOW_AUTH_DISABLED` en `{1, true, yes, on}`), y en ese caso la identidad es `DEV_CLAIMS = {sub: "dev", email: "dev@localhost", role: "dev"}`.

### Buckets de rate limit (ventana deslizante de 60 s)

| Bucket | Límite por defecto | Variable de entorno | Valor efectivo en esta generación |
| --- | --- | --- | --- |
| `anon` | 60/min | `RATE_LIMIT_ANON_PER_MIN` | 60/min |
| `auth` | 600/min | `RATE_LIMIT_AUTH_PER_MIN` | 600/min |
| `heavy` | 60/min | `RATE_LIMIT_HEAVY_PER_MIN` | 60/min |

Variables y defectos extraídos de `RateLimitMiddleware.__init__` con `ast` (no codificados a mano). Los límites son efectivos **por identidad**, no globales.

### Endpoints de cómputo pesado (`HEAVY_ENDPOINTS`)

```python
HEAVY_ENDPOINTS = (
    ("POST", "/api/v1/predictions"),
    ("POST", "/api/v1/backtests"),
    ("POST", "/api/v1/portfolio/analyze"),
    ("POST", "/api/v1/research"),
)
```

La clasificación es por **prefijo de ruta y método** (`request.url.path.startswith(prefix)`), no por ruta exacta: cualquier futuro `POST` bajo esos prefijos hereda el bucket `heavy`. `/health` y las rutas de documentación están exentas.

### Identidad del bucket

| Situación | Clave del limitador | Bucket |
| --- | --- | --- |
| `POST` bajo un prefijo de `HEAVY_ENDPOINTS` | `user:<sub>` o `ip:<client>` | `heavy` |
| Token verificado (firma+caducidad+audiencia OK) | `user:<sub>` | `auth` |
| Sin token, o token que **no** verifica | `ip:<client>` | `anon` |

La mera presencia de `Authorization` **no** eleva el bucket: `app/authz.py::token_identity` sólo devuelve claims de un token que verificó de verdad. `client` se resuelve con `client_identity()` y sólo honra `X-Forwarded-For` si el par directo está en `TRUSTED_PROXIES` (por defecto vacío: cabecera ignorada).

### Backend del limitador

- Con `REDIS_URL` que responda: ventana deslizante atómica en Redis (`app/rate_limit.py::RedisSlidingWindow`), compartida entre réplicas.
- Sin `REDIS_URL`, o si Redis falla: ventana en proceso (`MemorySlidingWindow`), **por réplica**, con log ruidoso. La degradación dura `RATE_LIMIT_REDIS_COOLDOWN_S` segundos (defecto 10, leído de `app/rate_limit.py`).

### Cabeceras que añade el middleware

| Cabecera | Añadida por | Valor |
| --- | --- | --- |
| `X-Request-ID` | `RequestIDMiddleware` | el `X-Request-ID` del cliente si cumple `^[A-Za-z0-9._:-]{1,128}$`, si no un `uuid4().hex` |
| `X-Process-Time` | `RequestIDMiddleware` | segundos con 4 decimales |
| `X-RateLimit-Limit` | `RateLimitMiddleware` | límite del bucket aplicado |
| `X-RateLimit-Remaining` | `RateLimitMiddleware` | peticiones restantes en la ventana |
| `Retry-After` | `RateLimitMiddleware` | sólo en 429 |

### Orden del middleware

`add_middleware` inserta por fuera, y `app/main.py` los añade de dentro a fuera. Orden de ejecución real (exterior → interior):

```
CORS → RequestID → AuditLog → RateLimit → Auth → router
```

El rate limit va **por fuera** de auth a propósito: el bucket anónimo se aplica antes de verificar credenciales, y como la identidad del bucket sale de un token ya verificado, un token inválido cae en `anon` y luego recibe 401.

<!-- BEGIN RUTAS (generado) -->

## Catálogo de rutas

### `/health`

#### `GET /health`

| Campo | Valor |
| --- | --- |
| tag | `ops` |
| summary | Health |
| operationId | `health_health_get` |

**Parámetros**: ninguno.

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |

### `/api/v1/assets`

#### `GET /api/v1/assets`

| Campo | Valor |
| --- | --- |
| tag | `assets` |
| summary | List Assets |
| operationId | `list_assets_api_v1_assets_get` |

**Parámetros**

| Nombre | En | Obligatorio | Tipo | Defecto | Restricciones | Descripción |
| --- | --- | --- | --- | --- | --- | --- |
| `query` | query | no | `string` | `""` | — | ticker/name search |
| `type` | query | no | `string` | `""` | — | STOCK\|ETF\|INDEX\|CRYPTO\|FUTURE |
| `sector` | query | no | `string` | `""` | — |  |
| `limit` | query | no | `integer` | `50` | min=1; max=200 |  |

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/assets/{ticker}`

#### `GET /api/v1/assets/{ticker}`

| Campo | Valor |
| --- | --- |
| tag | `assets` |
| summary | Asset Detail |
| operationId | `asset_detail_api_v1_assets__ticker__get` |

**Parámetros**

| Nombre | En | Obligatorio | Tipo | Defecto | Restricciones | Descripción |
| --- | --- | --- | --- | --- | --- | --- |
| `ticker` | path | sí | `string` | — | — |  |

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/assets/{ticker}/prices`

#### `GET /api/v1/assets/{ticker}/prices`

| Campo | Valor |
| --- | --- |
| tag | `assets` |
| summary | Asset Prices |
| operationId | `asset_prices_api_v1_assets__ticker__prices_get` |

**Parámetros**

| Nombre | En | Obligatorio | Tipo | Defecto | Restricciones | Descripción |
| --- | --- | --- | --- | --- | --- | --- |
| `ticker` | path | sí | `string` | — | — |  |
| `range` | query | no | `string` | `"1y"` | — | 1d\|5d\|1mo\|3mo\|6mo\|1y\|2y\|5y\|10y\|max |
| `interval` | query | no | `string` | `"1d"` | — | daily bars only |

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/assets/{ticker}/fundamentals`

#### `GET /api/v1/assets/{ticker}/fundamentals`

| Campo | Valor |
| --- | --- |
| tag | `assets` |
| summary | Asset Fundamentals |
| operationId | `asset_fundamentals_api_v1_assets__ticker__fundamentals_get` |

**Parámetros**

| Nombre | En | Obligatorio | Tipo | Defecto | Restricciones | Descripción |
| --- | --- | --- | --- | --- | --- | --- |
| `ticker` | path | sí | `string` | — | — |  |

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/assets/{ticker}/technicals`

#### `GET /api/v1/assets/{ticker}/technicals`

| Campo | Valor |
| --- | --- |
| tag | `assets` |
| summary | Asset Technicals |
| operationId | `asset_technicals_api_v1_assets__ticker__technicals_get` |

**Parámetros**

| Nombre | En | Obligatorio | Tipo | Defecto | Restricciones | Descripción |
| --- | --- | --- | --- | --- | --- | --- |
| `ticker` | path | sí | `string` | — | — |  |

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/predictions/{ticker}`

#### `GET /api/v1/predictions/{ticker}`

| Campo | Valor |
| --- | --- |
| tag | `predictions` |
| summary | Predictions For Ticker |
| operationId | `predictions_for_ticker_api_v1_predictions__ticker__get` |

**Parámetros**

| Nombre | En | Obligatorio | Tipo | Defecto | Restricciones | Descripción |
| --- | --- | --- | --- | --- | --- | --- |
| `ticker` | path | sí | `string` | — | — |  |
| `horizons` | query | no | `string` | `"1,5,20,63"` | — | comma-separated horizons |

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/predictions`

#### `GET /api/v1/predictions`

| Campo | Valor |
| --- | --- |
| tag | `predictions` |
| summary | Predictions Filtered |
| operationId | `predictions_filtered_api_v1_predictions_get` |

**Parámetros**

| Nombre | En | Obligatorio | Tipo | Defecto | Restricciones | Descripción |
| --- | --- | --- | --- | --- | --- | --- |
| `min_score` | query | no | `integer` | `0` | min=0; max=100 |  |
| `min_p_positive` | query | no | `number` | `0.0` | min=0.0; max=1.0 |  |
| `horizon` | query | no | `integer` | `20` | — |  |

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

#### `POST /api/v1/predictions`

| Campo | Valor |
| --- | --- |
| tag | `predictions` |
| summary | Request Prediction |
| operationId | `request_prediction_api_v1_predictions_post` |

**Parámetros**: ninguno.

**Cuerpo de la petición**

- obligatorio: sí
- `Content-Type`: `application/json`
- esquema: [`PredictionRequest`](#esquema-predictionrequest)

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 202 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/jobs/{job_id}`

#### `GET /api/v1/jobs/{job_id}`

| Campo | Valor |
| --- | --- |
| tag | `predictions` |
| summary | Job Status |
| operationId | `job_status_api_v1_jobs__job_id__get` |

**Parámetros**

| Nombre | En | Obligatorio | Tipo | Defecto | Restricciones | Descripción |
| --- | --- | --- | --- | --- | --- | --- |
| `job_id` | path | sí | `string` | — | — |  |

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/signals`

#### `GET /api/v1/signals`

| Campo | Valor |
| --- | --- |
| tag | `signals` |
| summary | List Signals |
| operationId | `list_signals_api_v1_signals_get` |

**Parámetros**

| Nombre | En | Obligatorio | Tipo | Defecto | Restricciones | Descripción |
| --- | --- | --- | --- | --- | --- | --- |
| `types` | query | no | `string` | `""` | — | comma-separated signal types |
| `min_strength` | query | no | `number` | `0.0` | min=0.0; max=1.0 |  |

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/scanner`

#### `GET /api/v1/scanner`

| Campo | Valor |
| --- | --- |
| tag | `signals` |
| summary | Scanner |
| operationId | `scanner_api_v1_scanner_get` |

**Parámetros**

| Nombre | En | Obligatorio | Tipo | Defecto | Restricciones | Descripción |
| --- | --- | --- | --- | --- | --- | --- |
| `setup` | query | no | `string` | `"momentum_breakout"` | — |  |
| `horizon` | query | no | `integer` | `20` | — |  |
| `limit` | query | no | `integer` | `25` | min=1; max=100 |  |

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/market-regime`

#### `GET /api/v1/market-regime`

| Campo | Valor |
| --- | --- |
| tag | `signals` |
| summary | Market Regime |
| operationId | `market_regime_api_v1_market_regime_get` |

**Parámetros**: ninguno.

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |

### `/api/v1/market-regime/history`

#### `GET /api/v1/market-regime/history`

| Campo | Valor |
| --- | --- |
| tag | `signals` |
| summary | Market Regime History |
| operationId | `market_regime_history_api_v1_market_regime_history_get` |

**Parámetros**

| Nombre | En | Obligatorio | Tipo | Defecto | Restricciones | Descripción |
| --- | --- | --- | --- | --- | --- | --- |
| `days` | query | no | `integer` | `365` | min=30; max=2000 |  |

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/etfs/compare`

#### `GET /api/v1/etfs/compare`

| Campo | Valor |
| --- | --- |
| tag | `etfs` |
| summary | Etf Compare |
| operationId | `etf_compare_api_v1_etfs_compare_get` |

**Parámetros**

| Nombre | En | Obligatorio | Tipo | Defecto | Restricciones | Descripción |
| --- | --- | --- | --- | --- | --- | --- |
| `tickers` | query | sí | `string` | — | — | e.g. QQQ,SPY |

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/etfs/{ticker}`

#### `GET /api/v1/etfs/{ticker}`

| Campo | Valor |
| --- | --- |
| tag | `etfs` |
| summary | Etf Profile |
| operationId | `etf_profile_api_v1_etfs__ticker__get` |

**Parámetros**

| Nombre | En | Obligatorio | Tipo | Defecto | Restricciones | Descripción |
| --- | --- | --- | --- | --- | --- | --- |
| `ticker` | path | sí | `string` | — | — |  |

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/portfolio/analyze`

#### `POST /api/v1/portfolio/analyze`

| Campo | Valor |
| --- | --- |
| tag | `portfolio` |
| summary | Analyze Portfolio |
| operationId | `analyze_portfolio_api_v1_portfolio_analyze_post` |

**Parámetros**: ninguno.

**Cuerpo de la petición**

- obligatorio: sí
- `Content-Type`: `application/json`
- esquema: [`PortfolioAnalyzeRequest`](#esquema-portfolioanalyzerequest)

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/backtests`

#### `POST /api/v1/backtests`

| Campo | Valor |
| --- | --- |
| tag | `backtests` |
| summary | Create Backtest |
| operationId | `create_backtest_api_v1_backtests_post` |

**Parámetros**: ninguno.

**Cuerpo de la petición**

- obligatorio: sí
- `Content-Type`: `application/json`
- esquema: [`BacktestRequest`](#esquema-backtestrequest)

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 202 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/backtests/{backtest_id}`

#### `GET /api/v1/backtests/{backtest_id}`

| Campo | Valor |
| --- | --- |
| tag | `backtests` |
| summary | Get Backtest |
| operationId | `get_backtest_api_v1_backtests__backtest_id__get` |

**Parámetros**

| Nombre | En | Obligatorio | Tipo | Defecto | Restricciones | Descripción |
| --- | --- | --- | --- | --- | --- | --- |
| `backtest_id` | path | sí | `string` | — | — |  |

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/models`

#### `GET /api/v1/models`

| Campo | Valor |
| --- | --- |
| tag | `models` |
| summary | List Models |
| operationId | `list_models_api_v1_models_get` |

**Parámetros**: ninguno.

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |

### `/api/v1/models/{version}/metrics`

#### `GET /api/v1/models/{version}/metrics`

| Campo | Valor |
| --- | --- |
| tag | `models` |
| summary | Model Metrics |
| operationId | `model_metrics_api_v1_models__version__metrics_get` |

**Parámetros**

| Nombre | En | Obligatorio | Tipo | Defecto | Restricciones | Descripción |
| --- | --- | --- | --- | --- | --- | --- |
| `version` | path | sí | `string` | — | — |  |
| `horizon` | query | no | `integer` | `20` | — |  |
| `group_by` | query | no | `string` | `"regime"` | — |  |

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/models/{version}/calibration`

#### `GET /api/v1/models/{version}/calibration`

| Campo | Valor |
| --- | --- |
| tag | `models` |
| summary | Model Calibration |
| operationId | `model_calibration_api_v1_models__version__calibration_get` |

**Parámetros**

| Nombre | En | Obligatorio | Tipo | Defecto | Restricciones | Descripción |
| --- | --- | --- | --- | --- | --- | --- |
| `version` | path | sí | `string` | — | — |  |
| `horizon` | query | no | `integer` | `20` | — |  |

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/research`

#### `POST /api/v1/research`

| Campo | Valor |
| --- | --- |
| tag | `research` |
| summary | Research |
| operationId | `research_api_v1_research_post` |

**Parámetros**: ninguno.

**Cuerpo de la petición**

- obligatorio: sí
- `Content-Type`: `application/json`
- esquema: [`ResearchRequest`](#esquema-researchrequest)

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/alerts`

#### `GET /api/v1/alerts`

| Campo | Valor |
| --- | --- |
| tag | `alerts` |
| summary | List Alerts |
| operationId | `list_alerts_api_v1_alerts_get` |

**Parámetros**

| Nombre | En | Obligatorio | Tipo | Defecto | Restricciones | Descripción |
| --- | --- | --- | --- | --- | --- | --- |
| `alert_type` | query | no | `string` | `""` | — |  |
| `ticker` | query | no | `string` | `""` | — |  |
| `limit` | query | no | `integer` | `50` | — |  |

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/alert-rules`

#### `GET /api/v1/alert-rules`

| Campo | Valor |
| --- | --- |
| tag | `alerts` |
| summary | List Alert Rules |
| operationId | `list_alert_rules_api_v1_alert_rules_get` |

**Parámetros**: ninguno.

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |

#### `POST /api/v1/alert-rules`

| Campo | Valor |
| --- | --- |
| tag | `alerts` |
| summary | Create Alert Rule |
| operationId | `create_alert_rule_api_v1_alert_rules_post` |

**Parámetros**: ninguno.

**Cuerpo de la petición**

- obligatorio: sí
- `Content-Type`: `application/json`
- esquema: [`AlertRuleCreate`](#esquema-alertrulecreate)

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 201 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/alert-rules/{rule_id}`

#### `DELETE /api/v1/alert-rules/{rule_id}`

| Campo | Valor |
| --- | --- |
| tag | `alerts` |
| summary | Delete Alert Rule |
| operationId | `delete_alert_rule_api_v1_alert_rules__rule_id__delete` |

**Parámetros**

| Nombre | En | Obligatorio | Tipo | Defecto | Restricciones | Descripción |
| --- | --- | --- | --- | --- | --- | --- |
| `rule_id` | path | sí | `string` | — | — |  |

**Cuerpo de la petición**: ninguno.

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/alerts/evaluate`

#### `POST /api/v1/alerts/evaluate`

| Campo | Valor |
| --- | --- |
| tag | `alerts` |
| summary | Evaluate Alerts |
| operationId | `evaluate_alerts_api_v1_alerts_evaluate_post` |

**Parámetros**: ninguno.

**Cuerpo de la petición**

- obligatorio: sí
- `Content-Type`: `application/json`
- esquema: [`EvaluateRequest`](#esquema-evaluaterequest)

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

### `/api/v1/alerts/{alert_id}/deliver`

#### `POST /api/v1/alerts/{alert_id}/deliver`

| Campo | Valor |
| --- | --- |
| tag | `alerts` |
| summary | Deliver Alert |
| operationId | `deliver_alert_api_v1_alerts__alert_id__deliver_post` |

**Parámetros**

| Nombre | En | Obligatorio | Tipo | Defecto | Restricciones | Descripción |
| --- | --- | --- | --- | --- | --- | --- |
| `alert_id` | path | sí | `string` | — | — |  |

**Cuerpo de la petición**

- obligatorio: sí
- `Content-Type`: `application/json`
- esquema: [`DeliverRequest`](#esquema-deliverrequest)

**Respuestas**

| Estado | Descripción | `Content-Type` | Esquema |
| --- | --- | --- | --- |
| 200 | Successful Response | `application/json` | `{}` (sin esquema: el handler devuelve un `dict` sin tipar) |
| 422 | Validation Error | `application/json` | [`HTTPValidationError`](#esquema-httpvalidationerror) |

<!-- END RUTAS (generado) -->

## Esquemas de componentes

Volcado literal de `components.schemas` en el OpenAPI de la app. Los esquemas `HTTPValidationError` / `ValidationError` los genera FastAPI para el 422; el resto son los modelos Pydantic usados como cuerpo de petición (`app/schemas.py` y modelos locales de los routers).

> **Limitación declarada:** los cuerpos de respuesta correctos aparecen como `{}` en el OpenAPI porque los handlers devuelven `dict` sin tipo anotado (el sobre se construye con `routers/__init__.py::env()`). El contrato de respuesta está documentado arriba (§Sobre de respuesta universal) y en `app/schemas.py`, no en el esquema OpenAPI.

### Esquema AlertRuleCreate

```json
{
  "properties": {
    "params": {
      "additionalProperties": true,
      "title": "Params",
      "type": "object"
    },
    "rule_type": {
      "title": "Rule Type",
      "type": "string"
    },
    "ticker": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ticker"
    }
  },
  "required": [
    "rule_type"
  ],
  "title": "AlertRuleCreate",
  "type": "object"
}
```

### Esquema BacktestRequest

```json
{
  "properties": {
    "allow_short": {
      "default": false,
      "title": "Allow Short",
      "type": "boolean"
    },
    "commission": {
      "default": 0.0,
      "title": "Commission",
      "type": "number"
    },
    "end": {
      "default": "2025-12-31",
      "title": "End",
      "type": "string"
    },
    "entry_rules": {
      "additionalProperties": true,
      "title": "Entry Rules",
      "type": "object"
    },
    "exit_rules": {
      "additionalProperties": true,
      "title": "Exit Rules",
      "type": "object"
    },
    "name": {
      "default": "stub-backtest",
      "title": "Name",
      "type": "string"
    },
    "position_sizing": {
      "additionalProperties": true,
      "title": "Position Sizing",
      "type": "object"
    },
    "slippage_bps": {
      "default": 5.0,
      "title": "Slippage Bps",
      "type": "number"
    },
    "start": {
      "default": "2024-01-01",
      "title": "Start",
      "type": "string"
    },
    "stop_loss": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Stop Loss"
    },
    "take_profit": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Take Profit"
    },
    "universe": {
      "items": {
        "type": "string"
      },
      "title": "Universe",
      "type": "array"
    }
  },
  "title": "BacktestRequest",
  "type": "object"
}
```

### Esquema Bar

```json
{
  "properties": {
    "close": {
      "title": "Close",
      "type": "number"
    },
    "high": {
      "title": "High",
      "type": "number"
    },
    "low": {
      "title": "Low",
      "type": "number"
    },
    "open": {
      "title": "Open",
      "type": "number"
    },
    "time": {
      "title": "Time",
      "type": "string"
    },
    "volume": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Volume"
    }
  },
  "required": [
    "time",
    "open",
    "high",
    "low",
    "close"
  ],
  "title": "Bar",
  "type": "object"
}
```

### Esquema DeliverRequest

```json
{
  "properties": {
    "channel": {
      "pattern": "^(push|email)$",
      "title": "Channel",
      "type": "string"
    },
    "to": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "To"
    }
  },
  "required": [
    "channel"
  ],
  "title": "DeliverRequest",
  "type": "object"
}
```

### Esquema EvaluateRequest

```json
{
  "properties": {
    "bars": {
      "anyOf": [
        {
          "items": {
            "$ref": "#/components/schemas/Bar"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Bars"
    },
    "context": {
      "additionalProperties": true,
      "title": "Context",
      "type": "object"
    },
    "rule_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Rule Id"
    },
    "ticker": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Ticker"
    }
  },
  "title": "EvaluateRequest",
  "type": "object"
}
```

### Esquema HTTPValidationError

```json
{
  "properties": {
    "detail": {
      "items": {
        "$ref": "#/components/schemas/ValidationError"
      },
      "title": "Detail",
      "type": "array"
    }
  },
  "title": "HTTPValidationError",
  "type": "object"
}
```

### Esquema PortfolioAnalyzeRequest

```json
{
  "properties": {
    "positions": {
      "items": {
        "$ref": "#/components/schemas/Position"
      },
      "title": "Positions",
      "type": "array"
    },
    "scenarios": {
      "items": {
        "type": "string"
      },
      "title": "Scenarios",
      "type": "array"
    }
  },
  "required": [
    "positions"
  ],
  "title": "PortfolioAnalyzeRequest",
  "type": "object"
}
```

### Esquema Position

```json
{
  "properties": {
    "cost_basis": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Cost Basis"
    },
    "qty": {
      "title": "Qty",
      "type": "number"
    },
    "ticker": {
      "title": "Ticker",
      "type": "string"
    }
  },
  "required": [
    "ticker",
    "qty"
  ],
  "title": "Position",
  "type": "object"
}
```

### Esquema PredictionRequest

```json
{
  "properties": {
    "horizons": {
      "items": {
        "type": "integer"
      },
      "title": "Horizons",
      "type": "array"
    },
    "model_version": {
      "default": "latest",
      "title": "Model Version",
      "type": "string"
    },
    "ticker": {
      "title": "Ticker",
      "type": "string"
    }
  },
  "required": [
    "ticker"
  ],
  "title": "PredictionRequest",
  "type": "object"
}
```

### Esquema ResearchRequest

```json
{
  "properties": {
    "question": {
      "maxLength": 2000,
      "minLength": 3,
      "title": "Question",
      "type": "string"
    }
  },
  "required": [
    "question"
  ],
  "title": "ResearchRequest",
  "type": "object"
}
```

### Esquema ValidationError

```json
{
  "properties": {
    "ctx": {
      "title": "Context",
      "type": "object"
    },
    "input": {
      "title": "Input"
    },
    "loc": {
      "items": {
        "anyOf": [
          {
            "type": "string"
          },
          {
            "type": "integer"
          }
        ]
      },
      "title": "Location",
      "type": "array"
    },
    "msg": {
      "title": "Message",
      "type": "string"
    },
    "type": {
      "title": "Error Type",
      "type": "string"
    }
  },
  "required": [
    "loc",
    "msg",
    "type"
  ],
  "title": "ValidationError",
  "type": "object"
}
```
