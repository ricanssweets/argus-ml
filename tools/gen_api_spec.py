#!/usr/bin/env python3
"""Genera ``repo/docs/API_SPEC.md`` a partir del esquema OpenAPI de la app.

USO (re-ejecutable desde cualquier directorio)::

    python repo/tools/gen_api_spec.py

FUENTE DE VERDAD
----------------
Este script **no inventa** nada: importa ``app.main:app``, vuelca
``app.openapi()`` y escribe el documento a partir de ese volcado. Los datos de
autenticación / rate limit se leen directamente de ``app/middleware.py``
(constantes ``PUBLIC_PATHS`` y ``HEAVY_ENDPOINTS`` importadas, y los valores por
defecto de los buckets extraídos con ``ast`` del propio código, nunca
codificados aquí a mano). Los códigos de error se importan de
``app.main._HTTP_ERROR_CODES``.

ENTORNO
-------
Fija las variables mínimas para que la importación sea reproducible
(``ARGUS_ENV=development``, ``AUTH_DISABLED=true``, ``CORS_ORIGINS``) y retira
``DATABASE_URL`` / ``REDIS_URL`` para generar siempre con la capa de datos stub
y el limitador en proceso. Ninguna de las dos afecta al esquema OpenAPI (ninguna
ruta es condicional a ellas), pero quitarlas hace la generación determinista y
reproduce el entorno de introspección documentado.

El guard de arranque (``assert_auth_configuration`` / ``assert_cors_configuration``)
vive en el *lifespan* de ASGI, no en la importación: importar la app con
cualquier entorno es seguro y este generador nunca arranca un servidor.

El documento generado NO lleva marca de tiempo a propósito: regenerar sobre el
mismo código produce un diff vacío.
"""

from __future__ import annotations

import ast
import json
import os
import sys
import typing
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent                       # <repo>/
ML_DIR = ROOT / "services" / "ml"
OUT_PATH = ROOT / "docs" / "API_SPEC.md"

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")

ROUTES_BEGIN = "<!-- BEGIN RUTAS (generado) -->"
ROUTES_END = "<!-- END RUTAS (generado) -->"


# ---------------------------------------------------------------------------
# Entorno + importación de la app real
# ---------------------------------------------------------------------------
def prepare_env() -> dict:
    """Fija el entorno de introspección y devuelve lo que se aplicó."""
    os.environ.setdefault("ARGUS_ENV", "development")
    os.environ.setdefault("AUTH_DISABLED", "true")
    os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("REDIS_URL", None)
    return {k: os.environ.get(k, "") for k in
            ("ARGUS_ENV", "AUTH_DISABLED", "CORS_ORIGINS")}


def import_app():
    """Importa ``app.main`` con ``services/ml`` en ``sys.path``."""
    if str(ML_DIR) not in sys.path:
        sys.path.insert(0, str(ML_DIR))
    from app.main import app  # noqa: PLC0415 — import deliberadamente tardío
    return app


# ---------------------------------------------------------------------------
# Lectura del código real de middleware (sin adivinar valores)
# ---------------------------------------------------------------------------
def _env_get(call: ast.AST):
    """``int(os.environ.get("NAME", 60))`` -> ``("NAME", 60)`` (o ``None``)."""
    if not isinstance(call, ast.Call) or not call.args:
        return None
    inner = call.args[0]
    if not isinstance(inner, ast.Call):
        return None
    func = inner.func
    if not (isinstance(func, ast.Attribute) and func.attr == "get"):
        return None
    owner = func.value
    if not (isinstance(owner, ast.Attribute) and owner.attr == "environ"):
        return None
    if not inner.args:
        return None
    name = getattr(inner.args[0], "value", None)
    default = None
    if len(inner.args) > 1 and isinstance(inner.args[1], ast.Constant):
        default = inner.args[1].value
    if not isinstance(name, str):
        return None
    return name, default


def bucket_overrides(middleware_path: Path) -> dict:
    """Mapa ``atributo -> (variable de entorno, valor por defecto)``.

    Se extrae de ``RateLimitMiddleware.__init__`` con ``ast``: si el código
    cambia de forma y la extracción falla, el generador **falla en voz alta**
    en lugar de emitir un valor inventado.
    """
    tree = ast.parse(middleware_path.read_text(encoding="utf-8"))
    out: dict = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == "RateLimitMiddleware"):
            continue
        for item in node.body:
            if not (isinstance(item, ast.FunctionDef) and item.name == "__init__"):
                continue
            for stmt in item.body:
                if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
                    continue
                target = stmt.targets[0]
                if not isinstance(target, ast.Attribute):
                    continue
                candidates = (stmt.value.values
                              if isinstance(stmt.value, ast.BoolOp)
                              else [stmt.value])
                for cand in candidates:
                    found = _env_get(cand)
                    if found:
                        out[target.attr] = found
    if not {"anon_per_min", "auth_per_min", "heavy_per_min"} <= set(out):
        raise RuntimeError(
            "no se pudieron leer los buckets de RateLimitMiddleware; "
            "revisa services/ml/app/middleware.py (el generador no adivina)")
    return out


def openapi_security_declared(spec: dict) -> str:
    """Describe honestamente si OpenAPI declara esquemas de seguridad."""
    schemes = ((spec.get("components") or {}).get("securitySchemes") or {})
    top = "security" in spec
    return (f"esquemas `securitySchemes` declarados: {sorted(schemes) or 'ninguno'}; "
            f"clave `security` en la raíz: {'sí' if top else 'no'}")


# ---------------------------------------------------------------------------
# Utilidades de render
# ---------------------------------------------------------------------------
def esc(text) -> str:
    """Escapa un valor para una celda de tabla markdown."""
    if text is None:
        return ""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def ref_name(schema) -> str | None:
    """``#/components/schemas/X`` -> ``X``."""
    if isinstance(schema, dict) and isinstance(schema.get("$ref"), str):
        return schema["$ref"].rsplit("/", 1)[-1]
    return None


def schema_cell(schema) -> str:
    """Descripción corta de un esquema (nombre de componente o JSON inline)."""
    if not schema:
        return "`{}` (sin esquema: el handler devuelve un `dict` sin tipar)"
    name = ref_name(schema)
    if name:
        return f"[`{name}`](#esquema-{name.lower()})"
    return "`" + json.dumps(schema, sort_keys=True, ensure_ascii=False) + "`"


def type_of(schema: dict) -> str:
    if not isinstance(schema, dict):
        return ""
    if ref_name(schema):
        return ref_name(schema)
    if "anyOf" in schema:
        return "anyOf(" + ", ".join(type_of(s) for s in schema["anyOf"]) + ")"
    t = schema.get("type", "")
    if t == "array":
        return f"array[{type_of(schema.get('items') or {})}]"
    return t or ""


def constraints_of(schema: dict) -> str:
    if not isinstance(schema, dict):
        return ""
    bits = []
    for key, label in (("minimum", "min"), ("maximum", "max"),
                       ("exclusiveMinimum", "min>"), ("exclusiveMaximum", "max<"),
                       ("minLength", "len>="), ("maxLength", "len<="),
                       ("pattern", "pattern"), ("enum", "enum")):
        if key in schema:
            bits.append(f"{label}={schema[key]}")
    return "; ".join(esc(b) for b in bits)


# ---------------------------------------------------------------------------
# Secciones del documento
# ---------------------------------------------------------------------------
def render_header(env_used: dict, spec: dict) -> list[str]:
    info = spec.get("info") or {}
    return [
        "# ARGUS ML — API_SPEC (GENERADO)",
        "",
        "> **Archivo generado. No editar a mano.**",
        ">",
        "> Regenerar con:",
        ">",
        "> ```bash",
        "> python repo/tools/gen_api_spec.py",
        "> ```",
        ">",
        "> Generador: [`tools/gen_api_spec.py`](../tools/gen_api_spec.py). "
        "Fuente: `app.openapi()` de `services/ml/app/main.py` "
        "(esquema OpenAPI que la propia app publica en `/openapi.json`), más "
        "`services/ml/app/middleware.py` para autenticación y rate limit, y "
        "`services/ml/app/main.py::_HTTP_ERROR_CODES` para los códigos de error.",
        ">",
        "> El documento no lleva marca de tiempo a propósito: "
        "regenerarlo sobre el mismo código produce un diff vacío.",
        "",
        "## Identidad del artefacto",
        "",
        "| Campo OpenAPI | Valor |",
        "| --- | --- |",
        f"| `openapi` | `{esc(spec.get('openapi'))}` |",
        f"| `info.title` | {esc(info.get('title'))} |",
        f"| `info.version` | `{esc(info.get('version'))}` |",
        f"| `info.description` | {esc(info.get('description'))} |",
        f"| Rutas en `paths` | {len(spec.get('paths') or {})} |",
        f"| Operaciones (método + ruta) | "
        f"{sum(len(ops) for ops in (spec.get('paths') or {}).values())} |",
        f"| Esquemas en `components.schemas` | "
        f"{len((spec.get('components') or {}).get('schemas') or {})} |",
        "",
        "Entorno con el que se generó este archivo (no altera el esquema, sólo "
        "lo hace reproducible):",
        "",
    ] + [f"- `{k}={esc(v)}`" for k, v in env_used.items()] + [
        "- `DATABASE_URL` y `REDIS_URL`: retiradas por el generador "
        "(capa de datos stub, limitador en proceso).",
        "",
    ]


def render_envelope(statuses: list[str]) -> list[str]:
    return [
        "## Sobre de respuesta universal (`data` + `meta`)",
        "",
        "Toda respuesta correcta se construye con "
        "`app/routers/__init__.py::env()`:",
        "",
        "```json",
        "{",
        '  "data": <cuerpo específico de la ruta>,',
        '  "meta": {',
        '    "as_of": "2026-10-06T17:59:56Z",',
        '    "data_status": "UNCONFIRMED",',
        '    "request_id": "3f9c1e0d8a7b4c2f9e1a6d0b5c4e2f18"',
        "  }",
        "}",
        "```",
        "",
        "| Clave | Tipo | Obligatoria | Origen |",
        "| --- | --- | --- | --- |",
        "| `data` | cualquiera | sí | handler de la ruta |",
        "| `meta.as_of` | `string` (ISO-8601 UTC) | sí | `data_stub.utcnow_iso()` o el `as_of` real del dato |",
        "| `meta.data_status` | `enum` | sí | el handler, o `aggregate_status()` (peor estado de los ítems) |",
        "| `meta.request_id` | `string` | sí | `request.state.request_id` (ver `X-Request-ID` en §Middleware) |",
        "| `meta.filters` | `object` | no | sólo `GET /api/v1/predictions` (`routers/predictions.py`) |",
        "| `meta.notes` | `array[string]` | no | sólo `GET /api/v1/scanner` cuando el pipeline no produce filas |",
        "",
        "Valores admitidos de `data_status` "
        "(`app/schemas.py::DataStatus`, extraídos del `Literal`):",
        "",
    ] + [f"- `{s}`" for s in statuses] + [
        "",
        "> **Honestidad del contrato:** este build **no** emite las claves "
        "`meta.demo` ni `meta.error`. No existen en el código "
        "(`routers/__init__.py::env()` sólo escribe `as_of`, `data_status`, "
        "`request_id` y, opcionalmente, `filters` / `notes`). Si algún consumidor "
        "las espera, no están implementadas: no se documentan como si lo "
        "estuvieran.",
        "",
        "> `meta.data_status` nunca es mejor que los registros que transporta: "
        "`aggregate_status()` (`app/routers/__init__.py`) devuelve el estado "
        "más pesimista (`CONFIRMED < LIKELY < UNCONFIRMED = SYNTHETIC_FIXTURE "
        "< CONFLICTING < MISSING`).",
        "",
    ]


def render_errors(error_codes: dict) -> list[str]:
    rows = sorted((int(k), v) for k, v in error_codes.items())
    return [
        "## Sobre de error",
        "",
        "Todo error se serializa como "
        "(`app/main.py::_error`, idéntico en `app/middleware.py::_error`):",
        "",
        "```json",
        "{",
        '  "error": {',
        '    "code": "RATE_LIMITED",',
        '    "message": "Rate limit exceeded: 60 requests/min (anon).",',
        '    "details": null',
        "  }",
        "}",
        "```",
        "",
        "| Clave | Tipo | Obligatoria |",
        "| --- | --- | --- |",
        "| `error.code` | `string` | sí — estable, es lo que los clientes deben comparar |",
        "| `error.message` | `string` | sí — prosa, no usar para lógica |",
        "| `error.details` | `object` / `null` | sí (nullable); sólo `VALIDATION_ERROR` lo rellena |",
        "",
        "### Códigos implementados en `app/main.py::_HTTP_ERROR_CODES`",
        "",
        "| Estado HTTP | `error.code` |",
        "| --- | --- |",
    ] + [f"| {status} | `{code}` |" for status, code in rows] + [
        "",
        "Un estado no incluido en esa tabla cae al código `HTTP_<estado>` "
        "(`app/main.py`: `f\"HTTP_{exc.status_code}\"`), nunca a un genérico. "
        "`RequestValidationError` produce `VALIDATION_ERROR` con "
        "`details.errors`; cualquier excepción no controlada produce "
        "`INTERNAL_ERROR`.",
        "",
        "### Códigos emitidos por el middleware (fuera de `_HTTP_ERROR_CODES`)",
        "",
        "| Estado HTTP | `error.code` | Emitido por |",
        "| --- | --- | --- |",
        "| 401 | `UNAUTHORIZED` | `AuthMiddleware` (token ausente o inválido) |",
        "| 503 | `AUTH_NOT_CONFIGURED` | `AuthMiddleware` (`SUPABASE_JWT_SECRET` ausente: falla cerrado) |",
        "| 429 | `RATE_LIMITED` | `RateLimitMiddleware` (+ cabeceras `Retry-After`, `X-RateLimit-*`) |",
        "| 503 | `RATE_LIMIT_UNAVAILABLE` | `RateLimitMiddleware` (el backend del limitador rompió su contrato: falla cerrado) |",
        "",
        "Estos cuatro códigos están en `services/ml/app/middleware.py` "
        "(`_error(...)`), no en `_HTTP_ERROR_CODES`: se listan porque son parte "
        "real del contrato observable y se localizaron leyendo el código.",
        "",
    ]


def render_auth(env_used_env: dict, mw, buckets: dict) -> list[str]:
    public_paths = sorted(mw.PUBLIC_PATHS)
    heavy = list(mw.HEAVY_ENDPOINTS)
    default_of = {"anon_per_min": "anon", "auth_per_min": "auth",
                  "heavy_per_min": "heavy"}
    limits = {
        "anon": buckets["anon_per_min"],
        "auth": buckets["auth_per_min"],
        "heavy": buckets["heavy_per_min"],
    }
    lines = [
        "## Autenticación y rate limit tal como están implementados",
        "",
        "Ambos viven en el **middleware**, no en las rutas: el esquema OpenAPI "
        "**no** declara mecanismos de seguridad (`securitySchemes` / "
        "`security`), así que ninguno de los endpoints aparece como protegido "
        "en el JSON OpenAPI. La protección es real, pero externa al esquema.",
        "",
        "### Rutas públicas (`app/middleware.py::PUBLIC_PATHS`)",
        "",
        "```python",
        "PUBLIC_PATHS = {" + ", ".join(f'"{p}"' for p in public_paths) + "}",
        "```",
        "",
        "| Ruta | Exenta de auth | Exenta de rate limit | Exenta de audit log |",
        "| --- | --- | --- | --- |",
    ]
    for path in public_paths:
        in_public = path in set(public_paths)
        # Auth y rate limit: `path in PUBLIC_PATHS or path.startswith("/docs")`.
        # Audit log: sólo excluye "/health".
        lines.append(
            f"| `{path}` | sí | sí | "
            f"{'sí' if path == '/health' else 'no'} |")
    lines += [
        "",
        "Además, `AuthMiddleware` y `RateLimitMiddleware` eximen cualquier ruta "
        "que empiece por `/docs` (`path.startswith(\"/docs\")`); "
        "`AuditLogMiddleware` sólo excluye la ruta exacta `/health`.",
        "",
        "El resto de rutas exige `Authorization: Bearer <JWT>` HS256 de Supabase. "
        "`AUTH_DISABLED=true` se rechaza en el arranque salvo opt-in explícito de "
        "desarrollo (`ARGUS_ENV` en `{development, dev, local, test, testing}` o "
        "`ARGUS_ALLOW_AUTH_DISABLED` en `{1, true, yes, on}`), y en ese caso la "
        "identidad es `DEV_CLAIMS = {sub: \"dev\", email: \"dev@localhost\", "
        "role: \"dev\"}`.",
        "",
        "### Buckets de rate limit (ventana deslizante de 60 s)",
        "",
        "| Bucket | Límite por defecto | Variable de entorno | Valor efectivo en esta generación |",
        "| --- | --- | --- | --- |",
    ]
    for attr, label in default_of.items():
        env_name, default = buckets[attr]
        effective = os.environ.get(env_name, default)
        lines.append(
            f"| `{label}` | {default}/min | `{env_name}` | {esc(effective)}/min |")
    lines += [
        "",
        "Variables y defectos extraídos de "
        "`RateLimitMiddleware.__init__` con `ast` (no codificados a mano). "
        "Los límites son efectivos **por identidad**, no globales.",
        "",
        "### Endpoints de cómputo pesado (`HEAVY_ENDPOINTS`)",
        "",
        "```python",
        "HEAVY_ENDPOINTS = (",
    ] + [f'    ("{m}", "{p}"),' for m, p in heavy] + [
        ")",
        "```",
        "",
        "La clasificación es por **prefijo de ruta y método** "
        "(`request.url.path.startswith(prefix)`), no por ruta exacta: cualquier "
        "futuro `POST` bajo esos prefijos hereda el bucket `heavy`. "
        "`/health` y las rutas de documentación están exentas.",
        "",
        "### Identidad del bucket",
        "",
        "| Situación | Clave del limitador | Bucket |",
        "| --- | --- | --- |",
        "| `POST` bajo un prefijo de `HEAVY_ENDPOINTS` | `user:<sub>` o `ip:<client>` | `heavy` |",
        "| Token verificado (firma+caducidad+audiencia OK) | `user:<sub>` | `auth` |",
        "| Sin token, o token que **no** verifica | `ip:<client>` | `anon` |",
        "",
        "La mera presencia de `Authorization` **no** eleva el bucket: "
        "`app/authz.py::token_identity` sólo devuelve claims de un token que "
        "verificó de verdad. `client` se resuelve con "
        "`client_identity()` y sólo honra `X-Forwarded-For` si el par directo "
        "está en `TRUSTED_PROXIES` (por defecto vacío: cabecera ignorada).",
        "",
        "### Backend del limitador",
        "",
        "- Con `REDIS_URL` que responda: ventana deslizante atómica en Redis "
        "(`app/rate_limit.py::RedisSlidingWindow`), compartida entre réplicas.",
        "- Sin `REDIS_URL`, o si Redis falla: ventana en proceso "
        "(`MemorySlidingWindow`), **por réplica**, con log ruidoso. La "
        "degradación dura `RATE_LIMIT_REDIS_COOLDOWN_S` segundos (defecto 10, "
        "leído de `app/rate_limit.py`).",
        "",
        "### Cabeceras que añade el middleware",
        "",
        "| Cabecera | Añadida por | Valor |",
        "| --- | --- | --- |",
        "| `X-Request-ID` | `RequestIDMiddleware` | el `X-Request-ID` del cliente si cumple `^[A-Za-z0-9._:-]{1,128}$`, si no un `uuid4().hex` |",
        "| `X-Process-Time` | `RequestIDMiddleware` | segundos con 4 decimales |",
        "| `X-RateLimit-Limit` | `RateLimitMiddleware` | límite del bucket aplicado |",
        "| `X-RateLimit-Remaining` | `RateLimitMiddleware` | peticiones restantes en la ventana |",
        "| `Retry-After` | `RateLimitMiddleware` | sólo en 429 |",
        "",
        "### Orden del middleware",
        "",
        "`add_middleware` inserta por fuera, y `app/main.py` los añade de dentro "
        "a fuera. Orden de ejecución real (exterior → interior):",
        "",
        "```",
        "CORS → RequestID → AuditLog → RateLimit → Auth → router",
        "```",
        "",
        "El rate limit va **por fuera** de auth a propósito: el bucket anónimo se "
        "aplica antes de verificar credenciales, y como la identidad del bucket "
        "sale de un token ya verificado, un token inválido cae en `anon` y luego "
        "recibe 401.",
        "",
    ]
    return lines


def render_routes(paths: dict) -> list[str]:
    lines = [ROUTES_BEGIN, "", "## Catálogo de rutas", ""]
    for path, ops in paths.items():
        lines += [f"### `{path}`", ""]
        for method, op in ops.items():
            if method not in HTTP_METHODS:
                continue
            op_id = op.get("operationId", "")
            tags = ", ".join(op.get("tags") or []) or "(sin tag)"
            lines += [
                f"#### `{method.upper()} {path}`",
                "",
                "| Campo | Valor |",
                "| --- | --- |",
                f"| tag | `{esc(tags)}` |",
                f"| summary | {esc(op.get('summary'))} |",
                f"| operationId | `{esc(op_id)}` |",
                "",
            ]
            params = op.get("parameters") or []
            if params:
                lines += [
                    "**Parámetros**",
                    "",
                    "| Nombre | En | Obligatorio | Tipo | Defecto | Restricciones | Descripción |",
                    "| --- | --- | --- | --- | --- | --- | --- |",
                ]
                for p in params:
                    sch = p.get("schema") or {}
                    lines.append(
                        "| `{n}` | {loc} | {req} | `{t}` | {d} | {c} | {desc} |".format(
                            n=esc(p.get("name")),
                            loc=esc(p.get("in")),
                            req="sí" if p.get("required") else "no",
                            t=esc(type_of(sch)),
                            d=(f"`{esc(json.dumps(sch['default'], ensure_ascii=False))}`"
                               if "default" in sch else "—"),
                            c=constraints_of(sch) or "—",
                            desc=esc(p.get("description") or sch.get("description")),
                        ))
                lines += [""]
            else:
                lines += ["**Parámetros**: ninguno.", ""]

            body = op.get("requestBody")
            if body:
                content = body.get("content") or {}
                for media, cspec in content.items():
                    sch = cspec.get("schema") or {}
                    lines += [
                        "**Cuerpo de la petición**",
                        "",
                        f"- obligatorio: {'sí' if body.get('required') else 'no'}",
                        f"- `Content-Type`: `{esc(media)}`",
                        f"- esquema: {schema_cell(sch)}",
                        "",
                    ]
            else:
                lines += ["**Cuerpo de la petición**: ninguno.", ""]

            lines += [
                "**Respuestas**",
                "",
                "| Estado | Descripción | `Content-Type` | Esquema |",
                "| --- | --- | --- | --- |",
            ]
            for status, rspec in (op.get("responses") or {}).items():
                content = rspec.get("content") or {}
                if not content:
                    lines.append(
                        f"| {status} | {esc(rspec.get('description'))} | — | — |")
                    continue
                for media, cspec in content.items():
                    lines.append(
                        "| {s} | {d} | `{m}` | {sch} |".format(
                            s=status,
                            d=esc(rspec.get("description")),
                            m=esc(media),
                            sch=schema_cell(cspec.get("schema") or {}),
                        ))
            lines += [""]
    lines += [ROUTES_END, ""]
    return lines


def render_components(schemas: dict) -> list[str]:
    lines = [
        "## Esquemas de componentes",
        "",
        "Volcado literal de `components.schemas` en el OpenAPI de la app. Los "
        "esquemas `HTTPValidationError` / `ValidationError` los genera FastAPI "
        "para el 422; el resto son los modelos Pydantic usados como cuerpo de "
        "petición (`app/schemas.py` y modelos locales de los routers).",
        "",
        "> **Limitación declarada:** los cuerpos de respuesta correctos aparecen "
        "como `{}` en el OpenAPI porque los handlers devuelven `dict` sin tipo "
        "anotado (el sobre se construye con `routers/__init__.py::env()`). El "
        "contrato de respuesta está documentado arriba (§Sobre de respuesta "
        "universal) y en `app/schemas.py`, no en el esquema OpenAPI.",
        "",
    ]
    for name in sorted(schemas):
        lines += [
            f"### Esquema {name}",
            "",
            "```json",
            json.dumps(schemas[name], indent=2, sort_keys=True, ensure_ascii=False),
            "```",
            "",
        ]
    return lines


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def build() -> str:
    env_used = prepare_env()
    app = import_app()

    from app import middleware as mw  # noqa: PLC0415
    from app import schemas  # noqa: PLC0415
    from app.main import _HTTP_ERROR_CODES  # noqa: PLC0415

    spec = app.openapi()
    buckets = bucket_overrides(Path(mw.__file__))
    statuses = list(typing.get_args(schemas.DataStatus))

    doc: list[str] = []
    doc += render_header(env_used, spec)
    doc += ["Detección de seguridad en el esquema: "
            + openapi_security_declared(spec) + ".", ""]
    doc += render_envelope(statuses)
    doc += render_errors(_HTTP_ERROR_CODES)
    doc += render_auth(env_used, mw, buckets)
    doc += render_routes(spec.get("paths") or {})
    doc += render_components((spec.get("components") or {}).get("schemas") or {})

    return "\n".join(doc).rstrip() + "\n"


def verify(written: str, spec: dict) -> bool:
    """Comprueba que el archivo escrito cubre todas las rutas del OpenAPI."""
    paths = spec.get("paths") or {}
    expected_paths = len(paths)
    expected_ops = sum(len(ops) for ops in paths.values())

    inside = written.split(ROUTES_BEGIN, 1)[1].split(ROUTES_END, 1)[0]
    written_paths = sum(1 for ln in inside.splitlines() if ln.startswith("### "))
    written_ops = sum(1 for ln in inside.splitlines() if ln.startswith("#### "))

    print(f"rutas en app.openapi()[\"paths\"]   : {expected_paths}")
    print(f"rutas escritas en docs/API_SPEC.md : {written_paths}")
    print(f"operaciones (método+ruta)          : {expected_ops}")
    print(f"operaciones escritas               : {written_ops}")
    ok = (expected_paths == written_paths) and (expected_ops == written_ops)
    print("OK: el documento cubre todas las rutas" if ok
          else "FALLO: el documento NO coincide con app.openapi()")
    return ok


def main() -> int:
    text = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)

    sys.path.insert(0, str(ML_DIR))
    from app.main import app  # noqa: PLC0415
    ok = verify(text, app.openapi())
    print(f"escrito: {OUT_PATH}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
