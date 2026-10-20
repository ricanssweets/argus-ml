# `docs/` — documentación derivada del código

Este directorio contiene la documentación de ingeniería del repositorio. Antes
no existía: el código citaba `docs/ARCHITECTURE.md`, `docs/API_SPEC.md`,
`docs/DATABASE_SCHEMA.md` y `docs/DEPLOYMENT_CHECKLIST.md`, y esos archivos no
estaban. Ahora existen, y su contenido se deriva del código y del SQL reales, no
de una intención previa.

## Índice

| Documento | Qué contiene | Origen |
| --- | --- | --- |
| [API_SPEC.md](./API_SPEC.md) | Catálogo completo de rutas (método, ruta, resumen, parámetros, cuerpo, respuestas), sobre `{data, meta}` / `{error}`, códigos de error nombrados, modelo de auth y rate limit con sus variables de entorno | **GENERADO** por [`tools/gen_api_spec.py`](../tools/gen_api_spec.py) a partir de `app.openapi()` + `app/middleware.py` + `app/main.py::_HTTP_ERROR_CODES` |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | Los tres artefactos desplegables, flujo de una petición y orden del middleware, capas de datos (Postgres / migraciones / SQLite / Redis / stub), registro de modelos y calibración, principio de degradación honesta, y el estado real de las «locked laws» del paquete cuantitativo | **Escrito a mano**, derivado de `services/ml/**`, `packages/quant/**` y `apps/web/**` |
| [DATABASE_SCHEMA.md](./DATABASE_SCHEMA.md) | Las 9 migraciones, 39 tablas, 59 índices, constraints, claves foráneas, políticas RLS, triggers, GRANT y semilla; qué se aplica y qué se salta | **Escrito a mano**, derivado 1:1 de `db/migrations/*.sql` y `db/seeds/*.sql` |
| [DEPLOYMENT_CHECKLIST.md](./DEPLOYMENT_CHECKLIST.md) | Tabla de variables de entorno con nombre real, propósito, obligatoriedad, valor por defecto, qué se rompe si falta; cómo arranca el contenedor; verificación post-despliegue | **Escrito a mano**, derivado de `grep os.environ`/`process.env` en `services/ml` y `apps/web`, más `render.yaml`, `start.sh` y ambos Dockerfiles |

## Generado vs. escrito a mano

- **Generado (no editar a mano):** `API_SPEC.md`. Se regenera con:

  ```bash
  python repo/tools/gen_api_spec.py
  ```

  El generador fija el entorno de introspección (`ARGUS_ENV=development`,
  `AUTH_DISABLED=true`, `CORS_ORIGINS`, sin `DATABASE_URL`/`REDIS_URL`),
  importa `app.main:app` —sin arrancar servidor, porque los guards de
  arranque viven en el lifespan y no en la importación— y escribe el archivo.
  No lleva marca de tiempo: regenerar sobre el mismo código produce un diff
  vacío. Sale con código 1 si el número de rutas/operaciones escritas no
  coincide con `app.openapi()["paths"]`.

- **Escritos a mano:** los otros tres. Si cambia el código o el SQL que
  documentan, hay que actualizarlos a mano; no hay generador que lo detecte.

## Cobertura de las citas que había repartidas por el código

| Cita en el código | Estado |
| --- | --- |
| `services/ml/app/main.py:3` — «Base path /api/v1 per API_SPEC.md» | **resuelta** → [API_SPEC.md](./API_SPEC.md) |
| `services/ml/app/middleware.py` — «per docs/API_SPEC.md» (buckets 60/600/min) | **resuelta** → [API_SPEC.md §Autenticación y rate limit](./API_SPEC.md#autenticación-y-rate-limit-tal-como-están-implementados) |
| `services/ml/app/cache.py`, `services/ml/app/routers/predictions.py` — «docs/PERFORMANCE.md» | **sigue sin resolverse**: ese documento no existe y no se crea aquí, porque no hay código que lo respalde más allá de la clave de caché `pred:{ticker}:{horizon}:{asof_date}` y su TTL (`PRED_CACHE_TTL`), ambos documentados en el checklist |
| `render.yaml:3` — «see docs/DEPLOYMENT_CHECKLIST.md Fase B» | **resuelta** → [DEPLOYMENT_CHECKLIST.md §Fase B](./DEPLOYMENT_CHECKLIST.md#fase-b--producción-render--contenedor) |
| `packages/quant/argus_quant/__init__.py:6` — «Locked laws (see docs/ARCHITECTURE.md)» | **resuelta** → [ARCHITECTURE.md §Las «locked laws»](./ARCHITECTURE.md#6-las-locked-laws-del-paquete-cuantitativo-y-su-estado-real) |
| `apps/web/README.md:5` — «Design authority: `~/workspace/argus/docs/` (ARCHITECTURE.md, DATABASE_SCHEMA.md, API_SPEC.md, MODEL_EVALUATION.md)» | **parcialmente resuelta**: tres de los cuatro existen ahora aquí; `MODEL_EVALUATION.md` **no existe** y no se crea (no hay evaluación de modelo publicada que documentar). La ruta `~/workspace/argus/docs/` no es este repositorio |
| `apps/web/lib/types.ts:1` — «mirror `~/workspace/argus/docs/API_SPEC.md`» | **parcialmente resuelta**: el contrato equivalente está en [API_SPEC.md](./API_SPEC.md), pero esa ruta concreta no existe |

## Qué NO se documenta aquí (y por qué)

- **`docs/PERFORMANCE.md`**: citado, inexistente. No se crea para no inventar
  cifras de rendimiento que nadie ha medido.
- **`docs/MODEL_EVALUATION.md`**: citado, inexistente. Las métricas reales
  disponibles viven en `services/ml/app/registry/` y se sirven por
  `GET /api/v1/models/{version}/metrics`; están descritas en
  [ARCHITECTURE.md §Registro de modelos y calibración](./ARCHITECTURE.md#4-registro-de-modelos-y-calibración).
- Cualquier especificación de producto previa al código: el repositorio no la
  tiene, y estos documentos describen **lo que el código hace**, incluidas sus
  limitaciones declaradas.
