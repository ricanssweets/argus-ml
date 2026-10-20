# ARGUS — DATABASE_SCHEMA (derivado del SQL real)

> **Documento escrito a mano, derivado 1:1 de los archivos SQL del repositorio.**
> Fuente única: [`db/migrations/001_extensions.sql`](../db/migrations/001_extensions.sql)
> … [`db/migrations/009_schema_hardening.sql`](../db/migrations/009_schema_hardening.sql)
> y [`db/seeds/calibration_maps_seed.sql`](../db/seeds/calibration_maps_seed.sql).
> No hay columnas inventadas: si una columna no aparece aquí, no existe en el SQL.
> No es generado automáticamente (a diferencia de
> [API_SPEC.md](./API_SPEC.md)); si el SQL cambia, este documento hay que
> actualizarlo a mano.

**Inventario verificado por conteo sobre los archivos:** 9 migraciones,
**39 tablas** (`CREATE TABLE IF NOT EXISTS`: 32 en `public`/`research` + 7 en `svc`),
**59 índices** (`CREATE INDEX IF NOT EXISTS`), **8 tablas convertidas en
hypertable** cuando TimescaleDB está disponible, 3 políticas RLS, 2 triggers.

Esquemas: `public` (datos de plataforma), `research` (backtesting aislado, creado
en 001), `svc` (tablas espejo de los sidecars de la aplicación, creadas en 009).

---

## 1. Qué migración se aplica y qué se salta

El runner es [`services/ml/app/migrate.py`](../services/ml/app/migrate.py).
Aplica `db/migrations/*.sql` en **orden alfabético** (001 → 009) y después el seed
`db/seeds/calibration_maps_seed.sql`, registrando cada archivo en el libro mayor
`schema_migrations`.

| Archivo | Contenido | Comportamiento del runner |
| --- | --- | --- |
| `001_extensions.sql` | `pgcrypto`, TimescaleDB opcional, función `argus_maybe_hypertable()`, esquema `research` | se aplica |
| `002_reference.sql` | 5 tablas de referencia | se aplica |
| `003_market_data.sql` | 8 tablas de datos de mercado | se aplica |
| `004_predictions.sql` | 10 tablas (predicciones, registro de modelos, observatorio) | se aplica |
| `005_portfolio_signals.sql` | 6 tablas (carteras, señales, alertas, logs) | se aplica |
| `006_research.sql` | 3 tablas en el esquema `research` | se aplica |
| `007_roles.sql` | rol `argus_app` + GRANT/REVOKE + COMMENT | **se intenta y normalmente queda `skipped`**: necesita `CREATE ROLE` (superusuario). Está en `SKIP_FILES`; al fallar se registra `status='skipped'` con el motivo y **no se reintenta** salvo `ARGUS_RETRY_SKIPPED=1`. En un Postgres gestionado (Neon/Render) el rol no existe y los GRANT de 009 tampoco se aplican |
| `008_phase4_9.sql` | marcador sin DDL (sólo un `RAISE NOTICE`) | se aplica (no-op) |
| `009_schema_hardening.sql` | esquema `svc` + 7 tablas espejo, 9 índices, RLS, triggers de inmutabilidad, FK `RESTRICT`, GRANT de `svc`, y 3 puntos documentados como NO aplicados | **nuevo en este árbol de trabajo y NO aplicado en este entorno** (ver abajo) |
| `seeds/calibration_maps_seed.sql` | 1 fila en `models`, 1 en `model_versions`, 8 en `calibration_maps` (`ON CONFLICT DO NOTHING`) | se aplica y se registra como `seeds/<nombre>` con su propio checksum (un seed modificado es DRIFT, no silencio) |

### Estado en este entorno (comprobado, no supuesto)

No hay `DATABASE_URL` definido en el entorno de trabajo, así que el runner no
aplica nada. Salida real de `python -m app.migrate` con `PYTHONPATH=services/ml`:

```
migrate: DATABASE_URL not set - skipping migrations
migrate: WARNING non-fatal (non-fatal): DATABASE_URL not set - skipping migrations - the API will start anyway and /health will report the database as unreachable
[exit=0]
```

Es decir: **en este entorno no está aplicada ninguna migración**, no hay libro
mayor que consultar, y `009` (nuevo) no se ha ejecutado nunca aquí. En un
despliegue con base de datos, el orden esperado es 001–006 y 008–009 `applied`,
007 `skipped`, más el seed.

### Mecánica del libro mayor `schema_migrations`

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    checksum   TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'applied',
    detail     TEXT,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'applied';
ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS detail TEXT;
```

- `checksum` = sha256 de los bytes aplicados. Igual → **SKIP**; distinto → **DRIFT**
  (se reporta y **no se reaplica**); fallo → no se registra y se reintenta.
- Cada archivo y su fila se escriben en **la misma transacción**.
- No bloquea el arranque por defecto (`fatal_mode()`); `MIGRATIONS_FATAL=1`
  activa el fallo rápido y `ARGUS_MIGRATE_OPTIONAL=1` lo desactiva siempre.

---

## 2. Extensiones, esquemas y funciones

| Objeto | Definición | Migración |
| --- | --- | --- |
| Extensión `pgcrypto` | `CREATE EXTENSION IF NOT EXISTS pgcrypto` (habilita `gen_random_uuid()`) | 001 |
| Extensión `timescaledb` | intento condicional: sólo si aparece en `pg_available_extensions`; si no, `RAISE NOTICE` y se sigue en Postgres plano | 001 |
| Función `argus_maybe_hypertable(tbl TEXT, time_col TEXT, chunk_us BIGINT) RETURNS void` | convierte en hypertable **sólo** si `timescaledb` está instalado en la base; si no, deja la tabla plana (los índices de cada migración sostienen las consultas). Idempotente (`if_not_exists => TRUE`) | 001 |
| Esquema `research` | `CREATE SCHEMA IF NOT EXISTS research` — almacén aislado de backtesting | 001 |
| Esquema `svc` | `CREATE SCHEMA IF NOT EXISTS svc` — tablas espejo de los sidecars | 009 |
| Función `argus_current_user_id() RETURNS uuid` | lee el GUC `argus.user_id`; devuelve `NULL` si no está puesto o no es un uuid válido (⇒ ninguna fila visible, INSERT rechazado: *fail closed*) | 009 |
| Función `argus_forbid_mutation() RETURNS trigger` | lanza `RAISE EXCEPTION ... ERRCODE = 'restrict_violation'` en cualquier UPDATE/DELETE | 009 |
| Rol `argus_app` | `CREATE ROLE argus_app NOLOGIN` dentro de un `DO` que comprueba `pg_roles` | 007 |

---

## 3. Esquema `public` — tablas de referencia (002)

### `users`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `id` | `UUID` | PK, `DEFAULT gen_random_uuid()` |
| `email` | `TEXT` | `NOT NULL`, `UNIQUE` |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` |

Espejo local del perfil; la autenticación la gestiona Supabase. Sin índices
adicionales (el `UNIQUE` de `email` ya crea uno).

### `assets`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `id` | `UUID` | PK, `DEFAULT gen_random_uuid()` |
| `ticker` | `TEXT` | `NOT NULL`, `UNIQUE` |
| `name` | `TEXT` | `NOT NULL` |
| `asset_type` | `TEXT` | `NOT NULL`, `CHECK (asset_type IN ('STOCK','ETF','INDEX','CRYPTO','FUTURE'))` |
| `exchange` | `TEXT` | — |
| `currency` | `TEXT` | `NOT NULL DEFAULT 'USD'` |
| `sector` | `TEXT` | — |
| `industry` | `TEXT` | — |
| `is_active` | `BOOLEAN` | `NOT NULL DEFAULT TRUE` |

Índices: `idx_assets_type (asset_type)`, `idx_assets_sector (sector)`.

### `stocks`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `asset_id` | `UUID` | PK, `REFERENCES assets(id) ON DELETE CASCADE` |
| `shares_outstanding` | `NUMERIC` | — |
| `float_shares` | `NUMERIC` | — |

### `etfs`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `asset_id` | `UUID` | PK, `REFERENCES assets(id) ON DELETE CASCADE` |
| `issuer` | `TEXT` | — |
| `expense_ratio` | `NUMERIC` | — |
| `aum` | `NUMERIC` | — |
| `inception_date` | `DATE` | — |
| `dividend_yield` | `DOUBLE PRECISION` | — |
| `tracking_index` | `TEXT` | — |

### `etf_holdings`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `etf_id` | `UUID` | `NOT NULL`, `REFERENCES etfs(asset_id) ON DELETE CASCADE` |
| `holding_asset_id` | `UUID` | `REFERENCES assets(id) ON DELETE SET NULL` |
| `holding_ticker` | `TEXT` | `NOT NULL` |
| `weight` | `NUMERIC` | `NOT NULL`, `CHECK (weight >= 0 AND weight <= 1)` |
| `as_of` | `DATE` | `NOT NULL` |

PK compuesta: `(etf_id, holding_ticker, as_of)`.
Índices: `idx_etf_holdings_etf (etf_id)`, `idx_etf_holdings_asset (holding_asset_id)`,
`idx_etf_holdings_asof (as_of DESC)`.

---

## 4. Esquema `public` — datos de mercado (003)

Hypertables si TimescaleDB está instalado (chunks de 7 días =
`7 * 86400000000` µs): `prices`, `options_chains`, `macro_data`, `sentiment`,
`market_regimes`.

### `prices` — barras OHLCV

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `time` | `TIMESTAMPTZ` | `NOT NULL` |
| `asset_id` | `UUID` | `NOT NULL`, `REFERENCES assets(id) ON DELETE CASCADE` |
| `open`, `high`, `low`, `close`, `adj_close` | `DOUBLE PRECISION` | — |
| `volume` | `BIGINT` | — |
| `data_source` | `TEXT` | — |
| `data_version` | `INT` | `NOT NULL DEFAULT 1` |

PK: `(time, asset_id)`. Hypertable sobre `time`. Índice `idx_prices_asset_time (asset_id, time DESC)`.

### `fundamentals` — snapshots point-in-time

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `id` | `UUID` | PK, `DEFAULT gen_random_uuid()` |
| `asset_id` | `UUID` | `NOT NULL`, `REFERENCES assets(id) ON DELETE CASCADE` |
| `fiscal_period` | `DATE` | `NOT NULL` |
| `reported_at` | `TIMESTAMPTZ` | `NOT NULL` |
| `revenue`, `eps`, `ebitda`, `fcf`, `total_debt`, `cash` | `NUMERIC` | — |
| `gross_margin`, `net_margin`, `roe`, `roic`, `interest_coverage` | `DOUBLE PRECISION` | — |
| `pe`, `forward_pe`, `peg`, `ev_ebitda`, `ps`, `pb`, `fcf_yield` | `DOUBLE PRECISION` | — |

`UNIQUE (asset_id, fiscal_period, reported_at)`.
Índices: `idx_fundamentals_asset (asset_id)`, `idx_fundamentals_period (asset_id, fiscal_period DESC)`.

### `earnings`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `id` | `UUID` | PK, `DEFAULT gen_random_uuid()` |
| `asset_id` | `UUID` | `NOT NULL`, `REFERENCES assets(id) ON DELETE CASCADE` |
| `announce_date` | `TIMESTAMPTZ` | `NOT NULL` |
| `fiscal_quarter` | `TEXT` | — |
| `eps_actual`, `eps_estimate`, `revenue_actual`, `revenue_estimate` | `NUMERIC` | — |
| `surprise_pct` | `DOUBLE PRECISION` | — |
| `guidance_text` | `TEXT` | — |

Índices: `idx_earnings_asset (asset_id)`, `idx_earnings_date (asset_id, announce_date DESC)`.

### `options_chains`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `time` | `TIMESTAMPTZ` | `NOT NULL` |
| `asset_id` | `UUID` | `NOT NULL`, `REFERENCES assets(id) ON DELETE CASCADE` |
| `expiration` | `DATE` | `NOT NULL` |
| `strike` | `DOUBLE PRECISION` | `NOT NULL` |
| `option_type` | `TEXT` | `NOT NULL`, `CHECK (option_type IN ('CALL','PUT'))` |
| `iv`, `delta`, `gamma`, `theta`, `vega` | `DOUBLE PRECISION` | — |
| `open_interest`, `volume` | `BIGINT` | — |

Sin PK declarada. Hypertable sobre `time`.
Índices: `idx_options_asset_time (asset_id, time DESC)`, `idx_options_expiry (asset_id, expiration, strike)`.

### `macro_data` — series tipo FRED

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `time` | `TIMESTAMPTZ` | `NOT NULL` |
| `series_id` | `TEXT` | `NOT NULL` |
| `value` | `DOUBLE PRECISION` | — |
| `source` | `TEXT` | — |

Sin PK. Hypertable sobre `time`. Índice `idx_macro_series_time (series_id, time DESC)`.

### `news`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `id` | `UUID` | PK, `DEFAULT gen_random_uuid()` |
| `asset_id` | `UUID` | `REFERENCES assets(id) ON DELETE CASCADE` (nullable) |
| `published_at` | `TIMESTAMPTZ` | `NOT NULL` |
| `source` | `TEXT` | — |
| `headline` | `TEXT` | `NOT NULL` |
| `url` | `TEXT` | — |
| `symbols` | `TEXT[]` | — |

Índices: `idx_news_asset (asset_id)`, `idx_news_pub (published_at DESC)`.

### `sentiment`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `time` | `TIMESTAMPTZ` | `NOT NULL` |
| `asset_id` | `UUID` | `REFERENCES assets(id) ON DELETE CASCADE` (nullable) |
| `scope` | `TEXT` | `NOT NULL`, `CHECK (scope IN ('ASSET','SECTOR','MARKET'))` |
| `polarity`, `magnitude`, `velocity` | `DOUBLE PRECISION` | — |
| `news_count` | `INT` | — |
| `source_model`, `model_version` | `TEXT` | — |

Sin PK. Hypertable sobre `time`.
Índices: `idx_sentiment_asset_time (asset_id, time DESC)`, `idx_sentiment_scope_time (scope, time DESC)`.

### `market_regimes`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `time` | `TIMESTAMPTZ` | `NOT NULL` |
| `regime` | `TEXT` | `NOT NULL` |
| `confidence` | `DOUBLE PRECISION` | — |
| `drivers` | `JSONB` | — |

Sin PK. Hypertable sobre `time`. Índice `idx_regimes_time (time DESC)`.

---

## 5. Esquema `public` — predicciones y registro de modelos (004)

### `models`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `id` | `UUID` | PK, `DEFAULT gen_random_uuid()` |
| `name` | `TEXT` | `NOT NULL` |
| `family` | `TEXT` | `NOT NULL`, `CHECK (family IN ('XGB','LGBM','RF','ELASTICNET','LOGRREG','SVM','MLP','LSTM','TCN','TRANSFORMER','STACKER'))` |
| `target_horizon` | `INT` | `NOT NULL` |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` |

### `model_versions`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `id` | `UUID` | PK, `DEFAULT gen_random_uuid()` |
| `model_id` | `UUID` | `NOT NULL`, `REFERENCES models(id) ON DELETE CASCADE` |
| `version` | `TEXT` | `NOT NULL`, `UNIQUE` |
| `status` | `TEXT` | `NOT NULL`, `CHECK (status IN ('EXPERIMENT','STAGING','CHALLENGER','CHAMPION','RETIRED'))` |
| `trained_on_range` | `TSTZRANGE` | — |
| `data_version` | `INT` | — |
| `hyperparams` | `JSONB` | — |
| `promoted_at` | `TIMESTAMPTZ` | — |
| `promotion_basis` | `TEXT` | — |
| `metrics` | `JSONB` | — |

Índices: `idx_model_versions_model (model_id)`, `idx_model_versions_status (status)`.

### `features` — store point-in-time

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `as_of` | `TIMESTAMPTZ` | `NOT NULL` |
| `asset_id` | `UUID` | `NOT NULL`, `REFERENCES assets(id) ON DELETE CASCADE` |
| `horizon` | `INT` | — |
| `engine` | `CHAR(1)` | `NOT NULL`, `CHECK (engine IN ('A','B','C','D','E','F','G','H'))` |
| `feature_name` | `TEXT` | `NOT NULL` |
| `feature_value` | `DOUBLE PRECISION` | — |
| `data_status` | `TEXT` | `NOT NULL DEFAULT 'UNCONFIRMED'`, `CHECK (data_status IN ('CONFIRMED','LIKELY','UNCONFIRMED','CONFLICTING','MISSING'))` |

PK: `(as_of, asset_id, engine, feature_name)` — **no incluye `horizon`** (ver
§11.c). Hypertable sobre `as_of`.
Índices: `idx_features_asset_asof (asset_id, as_of DESC)`,
`idx_features_asof_brin USING BRIN (as_of)`.

### `predictions` — registro bloqueado, append-only

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `id` | `UUID` | PK, `DEFAULT gen_random_uuid()` |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` |
| `as_of` | `TIMESTAMPTZ` | `NOT NULL` |
| `asset_id` | `UUID` | `NOT NULL`, `REFERENCES assets(id) ON DELETE CASCADE` (ver §11, cambia a `RESTRICT` en 009) |
| `horizon` | `INT` | `NOT NULL` |
| `model_version` | `TEXT` | `NOT NULL` (**sin** FK a propósito, ver §11.b) |
| `regime` | `TEXT` | — |
| `p_positive`, `p_negative` | `DOUBLE PRECISION` | — |
| `expected_return`, `expected_vol`, `expected_high`, `expected_low` | `DOUBLE PRECISION` | — |
| `ci_lower`, `ci_upper` | `DOUBLE PRECISION` | — |
| `downside_risk`, `upside_potential`, `risk_reward` | `DOUBLE PRECISION` | — |
| `composite_score` | `DOUBLE PRECISION` | — |
| `trend_strength`, `momentum_score`, `fundamental_score`, `risk_score`, `volatility_score` | `DOUBLE PRECISION` | — |
| `confidence` | `DOUBLE PRECISION` | — |
| `engine_signals` | `JSONB` | — |
| `explanation` | `JSONB` | — |
| `invalidation` | `JSONB` | — |
| `feature_hash` | `TEXT` | — |
| `data_version` | `INT` | — |
| `data_status_overall` | `TEXT` | `NOT NULL DEFAULT 'UNCONFIRMED'`, `CHECK (data_status_overall IN ('CONFIRMED','LIKELY','UNCONFIRMED','CONFLICTING','MISSING'))` |

`UNIQUE (as_of, asset_id, horizon, model_version)` — una fila bloqueada por
combinación; las correcciones son filas nuevas.
Índices: `idx_predictions_asset (asset_id)`,
`idx_predictions_asset_asof (asset_id, as_of DESC)`,
`idx_predictions_version (model_version)`.
Trigger `trg_predictions_append_only` desde 009.

### `prediction_outcomes`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `id` | `UUID` | PK, `DEFAULT gen_random_uuid()` |
| `prediction_id` | `UUID` | `NOT NULL`, `UNIQUE`, `REFERENCES predictions(id) ON DELETE CASCADE` |
| `observed_at` | `TIMESTAMPTZ` | `NOT NULL` |
| `actual_return`, `actual_high`, `actual_low` | `DOUBLE PRECISION` | — |
| `direction_correct` | `BOOLEAN` | — |
| `brier_contrib` | `DOUBLE PRECISION` | — |

Índice `idx_outcomes_prediction (prediction_id)`. Trigger
`trg_prediction_outcomes_append_only` desde 009.

### `engine_weights`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `model_version` | `TEXT` | `NOT NULL`, `REFERENCES model_versions(version) ON DELETE CASCADE` |
| `regime` | `TEXT` | `NOT NULL` |
| `engine` | `CHAR(1)` | `NOT NULL`, `CHECK (engine IN ('A','B','C','D','E','F','G','H'))` |
| `horizon` | `INT` | `NOT NULL` |
| `weight` | `DOUBLE PRECISION` | `NOT NULL` |

PK: `(model_version, regime, engine, horizon)`. Índice
`idx_engine_weights_version (model_version)`.

### `calibration_maps`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `id` | `UUID` | PK, `DEFAULT gen_random_uuid()` |
| `model_version` | `TEXT` | `NOT NULL`, `REFERENCES model_versions(version) ON DELETE CASCADE` |
| `horizon` | `INT` | `NOT NULL` |
| `method` | `TEXT` | `NOT NULL`, `CHECK (method IN ('ISOTONIC','PLATT'))` |
| `buckets` | `JSONB` | `NOT NULL` |
| `fitted_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` |
| `min_samples` | `INT` | `NOT NULL`, `CHECK (min_samples >= 20)` |

`UNIQUE (model_version, horizon)`. Índice `idx_calibration_version (model_version)`.
El `CHECK (min_samples >= 20)` es la ley `MIN_SAMPLES_PER_BUCKET` impuesta en la
base de datos.

### `training_runs`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `id` | `UUID` | PK, `DEFAULT gen_random_uuid()` |
| `model_version` | `TEXT` | `NOT NULL` (sin FK) |
| `started_at` | `TIMESTAMPTZ` | `NOT NULL` |
| `finished_at` | `TIMESTAMPTZ` | — |
| `config` | `JSONB` | — |
| `metrics` | `JSONB` | — |
| `leakage_checks_passed` | `BOOLEAN` | — |
| `seed` | `INT` | — |
| `git_sha` | `TEXT` | — |

Índice `idx_training_runs_version (model_version)`.

### `model_metrics_daily`

`day TIMESTAMPTZ NOT NULL`, `model_version TEXT NOT NULL`, `horizon INT NOT NULL`,
`regime TEXT`, `sector TEXT`, `n INT`, y `DOUBLE PRECISION`:
`accuracy`, `precision`, `recall`, `f1`, `roc_auc`, `brier`, `log_loss`,
`cal_error`, `mae`, `rmse`, `directional_accuracy`.

Sin PK. Hypertable sobre `day`.
Índices: `idx_metrics_version_day (model_version, day DESC)`, `idx_metrics_day (day DESC)`.

### `degradation_flags`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `id` | `UUID` | PK, `DEFAULT gen_random_uuid()` |
| `detected_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` |
| `model_version` | `TEXT` | `NOT NULL` |
| `horizon` | `INT` | `NOT NULL` |
| `metric` | `TEXT` | `NOT NULL` |
| `baseline`, `current` | `DOUBLE PRECISION` | — |
| `window_days` | `INT` | `NOT NULL` |
| `severity` | `TEXT` | `NOT NULL`, `CHECK (severity IN ('WATCH','WARN','CRITICAL'))` |
| `acknowledged` | `BOOLEAN` | `NOT NULL DEFAULT FALSE` |

Índices: `idx_degradation_version (model_version)`, `idx_degradation_detected (detected_at DESC)`.

---

## 6. Esquema `public` — carteras, señales, alertas y logs (005)

### `portfolios`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `id` | `UUID` | PK, `DEFAULT gen_random_uuid()` |
| `user_id` | `UUID` | `NOT NULL`, `REFERENCES users(id) ON DELETE CASCADE` |
| `name` | `TEXT` | `NOT NULL` |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` |

Índice `idx_portfolios_user (user_id)`. **RLS activada y forzada** (009).

### `portfolio_positions`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `portfolio_id` | `UUID` | `NOT NULL`, `REFERENCES portfolios(id) ON DELETE CASCADE` |
| `asset_id` | `UUID` | `NOT NULL`, `REFERENCES assets(id) ON DELETE CASCADE` |
| `qty` | `NUMERIC` | `NOT NULL` |
| `cost_basis` | `NUMERIC` | — |

PK `(portfolio_id, asset_id)`.
Índices: `idx_positions_portfolio (portfolio_id)`, `idx_positions_asset (asset_id)`.
Sin RLS propia: depende del `portfolio_id` del dueño.

### `signals`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `id` | `UUID` | PK, `DEFAULT gen_random_uuid()` |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` |
| `as_of` | `TIMESTAMPTZ` | `NOT NULL` |
| `asset_id` | `UUID` | `NOT NULL`, `REFERENCES assets(id) ON DELETE CASCADE` |
| `signal_type` | `TEXT` | `NOT NULL` |
| `strength` | `DOUBLE PRECISION` | — |
| `horizon` | `INT` | — |
| `prediction_id` | `UUID` | `REFERENCES predictions(id) ON DELETE SET NULL` |
| `payload` | `JSONB` | — |

Índices: `idx_signals_asset`, `idx_signals_asof (as_of DESC)`, `idx_signals_type`,
`idx_signals_prediction`.

### `alerts`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `id` | `UUID` | PK, `DEFAULT gen_random_uuid()` |
| `user_id` | `UUID` | `NOT NULL`, `REFERENCES users(id) ON DELETE CASCADE` |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` |
| `alert_type` | `TEXT` | `NOT NULL` |
| `asset_id` | `UUID` | `REFERENCES assets(id) ON DELETE SET NULL` |
| `title` | `TEXT` | `NOT NULL` |
| `body` | `TEXT` | — |
| `severity` | `TEXT` | `NOT NULL DEFAULT 'INFO'` |
| `channel` | `TEXT` | `NOT NULL`, `CHECK (channel IN ('PUSH','EMAIL','IN_APP'))` |
| `delivered_at` | `TIMESTAMPTZ` | — |
| `read_at` | `TIMESTAMPTZ` | — |

Índices: `idx_alerts_user`, `idx_alerts_asset`, `idx_alerts_created (created_at DESC)`.
**RLS activada y forzada** (009).

> Ojo: la tabla `alerts` de `public` **no tiene** columna `rule_id` ni `ticker`;
> las equivalentes en `svc.alerts` sí. Son esquemas distintos a propósito (§9).

### `alert_rules`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `id` | `UUID` | PK, `DEFAULT gen_random_uuid()` |
| `user_id` | `UUID` | `NOT NULL`, `REFERENCES users(id) ON DELETE CASCADE` |
| `rule_type` | `TEXT` | `NOT NULL` |
| `asset_id` | `UUID` | `REFERENCES assets(id) ON DELETE CASCADE` |
| `params` | `JSONB` | `NOT NULL DEFAULT '{}'::jsonb` |
| `is_active` | `BOOLEAN` | `NOT NULL DEFAULT TRUE` |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` |

Índices: `idx_alert_rules_user`, `idx_alert_rules_asset`.
**RLS activada y forzada** (009).

### `system_logs`

`time TIMESTAMPTZ NOT NULL`, `level TEXT NOT NULL`, `service TEXT NOT NULL`,
`message TEXT NOT NULL`, `context JSONB`.

Sin PK. Hypertable sobre `time`.
Índices: `idx_logs_time (time DESC)`, `idx_logs_service (service, time DESC)`.
Es la tabla que escribe `AuditLogMiddleware` (`INSERT INTO system_logs (time,
level, service, message, context)`), best-effort y en un hilo de trabajo.

---

## 7. Esquema `research` — backtesting aislado (006)

### `research.backtests`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `id` | `UUID` | PK, `DEFAULT gen_random_uuid()` |
| `name` | `TEXT` | `NOT NULL` |
| `config` | `JSONB` | `NOT NULL` |
| `universe` | `TEXT[]` | — |
| `start_date` | `DATE` | `NOT NULL` |
| `end_date` | `DATE` | `NOT NULL` |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` |

### `research.backtest_trades`

| Columna | Tipo | Restricciones |
| --- | --- | --- |
| `id` | `UUID` | PK, `DEFAULT gen_random_uuid()` |
| `backtest_id` | `UUID` | `NOT NULL`, `REFERENCES research.backtests(id) ON DELETE CASCADE` |
| `asset_id` | `UUID` | `NOT NULL`, `REFERENCES public.assets(id) ON DELETE CASCADE` |
| `entry_time` | `TIMESTAMPTZ` | `NOT NULL` |
| `exit_time` | `TIMESTAMPTZ` | — |
| `side` | `TEXT` | `NOT NULL`, `CHECK (side IN ('LONG','SHORT'))` |
| `qty` | `NUMERIC` | `NOT NULL` |
| `entry_price` | `DOUBLE PRECISION` | `NOT NULL` |
| `exit_price` | `DOUBLE PRECISION` | — |
| `pnl`, `costs` | `NUMERIC` | — |
| `bars_held` | `INT` | — |
| `exit_reason` | `TEXT` | — |

Índices: `idx_bt_trades_backtest`, `idx_bt_trades_asset`,
`idx_bt_trades_entry (backtest_id, entry_time)`.

### `research.backtest_metrics`

`backtest_id UUID PRIMARY KEY REFERENCES research.backtests(id) ON DELETE CASCADE`,
`cagr`, `total_return`, `sharpe`, `sortino`, `max_drawdown`, `win_rate`,
`profit_factor`, `expectancy`, `recovery_factor`, `exposure`, `alpha`, `beta`,
`information_ratio` (`DOUBLE PRECISION`), `avg_win`, `avg_loss` (`NUMERIC`),
`n_trades` (`INT`), `monthly_returns` (`JSONB`), `benchmark` (`TEXT`).

---

## 8. Seguridad y privilegios (007)

| Sentencia | Efecto |
| --- | --- |
| `CREATE ROLE argus_app NOLOGIN` (guardado por `DO` sobre `pg_roles`) | rol de servicio sin login |
| `GRANT CONNECT ON DATABASE <current_database()> TO argus_app` | puede conectarse |
| `GRANT USAGE ON SCHEMA public, research TO argus_app` | puede usar ambos esquemas |
| `GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA public, research TO argus_app` | sólo lectura + inserción |
| `GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public, research TO argus_app` | secuencias |
| `REVOKE UPDATE, DELETE ON public.predictions FROM argus_app` | predicciones inmutables |
| `REVOKE UPDATE, DELETE ON public.prediction_outcomes FROM argus_app` | resultados escritos una vez |
| `ALTER DEFAULT PRIVILEGES IN SCHEMA public, research GRANT SELECT, INSERT …` | las tablas futuras heredan la postura |
| `COMMENT ON TABLE public.predictions` / `public.prediction_outcomes` | política documentada en la propia tabla |

**Limitación real y reconocida en el código:** estos `REVOKE` son inertes si la
aplicación se conecta como el rol administrador (que es lo que ocurre en un
Postgres gestionado donde 007 no puede crear el rol). Por eso 009 añade triggers,
que sí se aplican al dueño de la tabla.

---

## 9. Endurecimiento (009)

### 9.1 Esquema `svc` — tablas espejo

Definidas **dos veces**: en el SQL de 009 y en `MIRROR_DDL` de
[`app/db.py`](../services/ml/app/db.py) (que las crea ad hoc al arrancar cuando
hay `DATABASE_URL`). Deben permanecer sincronizadas; 009 existe precisamente para
que los grants, índices y RLS sean deterministas.

| Tabla | Columnas |
| --- | --- |
| `svc.research_assets` | `id TEXT PK`, `ticker TEXT NOT NULL UNIQUE`, `name`, `asset_type`, `sector`, `industry`, `sp500 INTEGER DEFAULT 0`, `pe_ratio REAL`, `data_status TEXT NOT NULL DEFAULT 'SYNTHETIC_FIXTURE'` |
| `svc.research_predictions` | `id TEXT PK`, `as_of TEXT NOT NULL`, `asset_id TEXT NOT NULL`, `horizon INTEGER NOT NULL`, `model_version TEXT NOT NULL`, `regime`, `p_positive`, `p_negative`, `expected_return`, `expected_vol`, `expected_high`, `expected_low`, `ci_lower`, `ci_upper`, `downside_risk`, `upside_potential`, `risk_reward`, `composite_score`, `trend_strength`, `momentum_score`, `fundamental_score`, `risk_score`, `volatility_score`, `confidence` (todos `REAL`), `engine_signals TEXT`, `explanation TEXT`, `data_status_overall TEXT NOT NULL DEFAULT 'SYNTHETIC_FIXTURE'`, `UNIQUE (as_of, asset_id, horizon, model_version)` |
| `svc.research_prediction_outcomes` | `id TEXT PK`, `prediction_id TEXT NOT NULL UNIQUE`, `observed_at TEXT NOT NULL`, `actual_return REAL`, `direction_correct INTEGER`, `data_status TEXT NOT NULL DEFAULT 'SYNTHETIC_FIXTURE'` |
| `svc.research_backtests` | `id TEXT PK`, `name TEXT NOT NULL`, `config TEXT`, `universe TEXT`, `start_date TEXT`, `end_date TEXT`, `created_at TEXT`, `cagr REAL`, `sharpe REAL`, `max_drawdown REAL`, `win_rate REAL`, `n_trades INTEGER`, `data_status TEXT NOT NULL DEFAULT 'SYNTHETIC_FIXTURE'` |
| `svc.research_model_metrics_daily` | `day TEXT NOT NULL`, `model_version TEXT NOT NULL`, `horizon INTEGER NOT NULL`, `regime`, `sector`, `n INTEGER`, `accuracy`, `precision`, `recall`, `f1`, `roc_auc`, `brier`, `log_loss`, `cal_error`, `mae`, `rmse`, `directional_accuracy` (`REAL`), `data_status TEXT NOT NULL DEFAULT 'SYNTHETIC_FIXTURE'`, `PRIMARY KEY (day, model_version, horizon, regime, sector)` |
| `svc.alert_rules` | `id TEXT PK`, `user_id TEXT NOT NULL DEFAULT 'local'`, `rule_type TEXT NOT NULL`, `asset_id TEXT`, `ticker TEXT`, `params TEXT NOT NULL DEFAULT '{}'`, `is_active INTEGER NOT NULL DEFAULT 1`, `created_at TEXT NOT NULL` |
| `svc.alerts` | `id TEXT PK`, `user_id TEXT NOT NULL DEFAULT 'local'`, `created_at TEXT NOT NULL`, `alert_type TEXT NOT NULL`, `asset_id TEXT`, `ticker TEXT`, `title TEXT NOT NULL`, `body TEXT`, `severity TEXT NOT NULL DEFAULT 'INFO'`, `channel TEXT NOT NULL DEFAULT 'IN_APP'`, `delivered_at TEXT`, `read_at TEXT`, `rule_id TEXT` |

`COMMENT ON TABLE svc.alerts` y `svc.alert_rules` documentan que las escribe
`app/routers/alerts.py` cuando hay `DATABASE_URL`.

### 9.2 Índices nuevos de `svc`

`idx_svc_alerts_user_created (user_id, created_at DESC)`,
`idx_svc_alerts_ticker (ticker, created_at DESC)`,
`idx_svc_alerts_unread (user_id) WHERE read_at IS NULL` (**parcial**),
`idx_svc_alert_rules_user_active (user_id, is_active)`,
`idx_svc_alert_rules_ticker (ticker)`,
`idx_svc_research_preds_asof (as_of DESC, asset_id)`,
`idx_svc_research_preds_asset (asset_id, horizon)`,
`idx_svc_research_bt_created (created_at DESC)`,
`idx_svc_research_metrics_day (day DESC, model_version)`.

### 9.3 Row Level Security

Se aplica a `public.portfolios`, `public.alerts` y `public.alert_rules`
(guardado con `to_regclass(...) IS NOT NULL`):

```sql
ALTER TABLE public.<t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.<t> FORCE ROW LEVEL SECURITY;
CREATE POLICY <t>_owner_isolation ON public.<t>
    USING (user_id = argus_current_user_id())
    WITH CHECK (user_id = argus_current_user_id());
```

- La identidad sale del GUC por transacción `argus.user_id`
  (`SELECT set_config('argus.user_id', $1, true)`), que **la API no establece
  hoy**: sin él `argus_current_user_id()` devuelve `NULL`, la política no casa
  con ninguna fila y los INSERT se rechazan.
- `FORCE` es deliberado: sin él, las políticas serían inertes para el rol dueño
  (el que realmente conecta).
- `COMMENT ON POLICY` documenta: «Tenant isolation: a session sees only its own
  rows (argus.user_id)».

### 9.4 Triggers de inmutabilidad

```sql
CREATE TRIGGER trg_predictions_append_only
    BEFORE UPDATE OR DELETE ON public.predictions
    FOR EACH ROW EXECUTE FUNCTION argus_forbid_mutation();
CREATE TRIGGER trg_prediction_outcomes_append_only
    BEFORE UPDATE OR DELETE ON public.prediction_outcomes
    FOR EACH ROW EXECUTE FUNCTION argus_forbid_mutation();
```

La función lanza con `ERRCODE = 'restrict_violation'`. No rompe al escritor
porque `app/db.py` usa `INSERT ... ON CONFLICT DO NOTHING` (nunca UPDATE).
**Limitación declarada en el propio SQL:** es un trigger por fila, así que
`TRUNCATE` **no** queda cubierto; para eso hay que revocar `TRUNCATE` al rol de
aplicación.

### 9.5 FK `predictions.asset_id`: `CASCADE` → `RESTRICT`

Un bloque `DO` busca en `pg_constraint` la FK de `predictions` sobre `asset_id`
con `confdeltype = 'c'` y, si existe, la sustituye por
`predictions_asset_id_restrict_fkey ... ON DELETE RESTRICT`. Así borrar un activo
no se lleva por delante predicciones bloqueadas. Idempotente: si ya es `RESTRICT`,
el bucle no encuentra nada.

### 9.6 GRANT de `svc`

Si `argus_app` existe: `GRANT USAGE ON SCHEMA svc`, `GRANT SELECT, INSERT ON ALL
TABLES IN SCHEMA svc`, `GRANT USAGE, SELECT ON ALL SEQUENCES`, más
`ALTER DEFAULT PRIVILEGES FOR ROLE <current_user> IN SCHEMA svc`. Si el rol no
existe (caso normal en Postgres gestionado), sólo emite un `RAISE NOTICE`: la
migración sigue siendo determinista.

---

## 10. Semilla de calibración

[`db/seeds/calibration_maps_seed.sql`](../db/seeds/calibration_maps_seed.sql) —
generada desde `services/ml/app/registry/calibration/*.json`, idempotente
(`ON CONFLICT DO NOTHING` en cada INSERT):

| Tabla | Filas | Contenido |
| --- | --- | --- |
| `models` | 1 | `id = 1111…1111`, `name = 'ARGUS-EQ'`, `family = 'STACKER'`, `target_horizon = 20` |
| `model_versions` | 1 | `id = 2222…2222`, `version = 'ARGUS-EQ-1.1'`, `status = 'CHAMPION'`, `promoted_at = now()`, `promotion_basis` textual |
| `calibration_maps` | **8** | un mapa `ISOTONIC` por horizonte (1, 3, 5, 10, 20, 63, 126, 252), `buckets` en JSON, `min_samples = 20` |

El seed se registra en el libro mayor como `seeds/calibration_maps_seed.sql`.
Sólo inserta `ARGUS-EQ-1.1`; `ARGUS-EQ-1.0` no existe en `model_versions` (es la
razón por la que `predictions.model_version` **no** lleva FK, ver §11.b).

---

## 11. Elementos documentados como NO aplicados (deliberados, en 009)

Estos tres puntos están escritos en el propio SQL con su DDL propuesto y el
motivo. Se documentan aquí sin aplicarlos, y **no** se describen como si
existieran.

**(a) RLS en `svc.alerts` / `svc.alert_rules`: NO activada.** Las escribe
`routers/alerts.py` con `user_id` derivado del sujeto verificado, pero la API no
establece el GUC `argus.user_id`; activar RLS *fail-closed* dejaría toda lectura
de alertas en cero filas y todo INSERT rechazado, sin ganancia de aislamiento.
`svc.research_*` no tiene columna de dueño, así que no aplica.

**(b) FK `predictions.model_version` → `model_versions(version)`: NO añadida.**
`model_version` se rellena desde el registro en **archivos**
(`ARGUS-EQ-1.0` y `ARGUS-EQ-1.1`), mientras que el seed sólo inserta
`ARGUS-EQ-1.1`. Una FK obligaría a rechazar predicciones válidas de versiones aún
no insertadas, convirtiendo una escritura correcta en un fallo. El índice
`idx_predictions_version` ya existe, así que el join no es el cuello de botella.

**(c) PK de `features` sin `horizon`: NO corregida.** La PK
`(as_of, asset_id, engine, feature_name)` omite `horizon`, así que dos filas de la
misma feature con distinto horizonte en el mismo `as_of` colisionan. Verificado en
el SQL/comentario: ningún código de `services/ml` inserta en `features`, de modo
que hoy la colisión no es alcanzable. El arreglo correcto exige reconstruir la
tabla (y `horizon` es anulable, por lo que no puede entrar en una PK sin más).

---

## 12. Divergencias y trampas conocidas (leídas del SQL)

1. **`data_status` en la API tiene 6 valores; en la base de datos, 5.** Los
   `CHECK` de `features.data_status` y `predictions.data_status_overall` admiten
   `CONFIRMED, LIKELY, UNCONFIRMED, CONFLICTING, MISSING`. `SYNTHETIC_FIXTURE`
   (que sí usa la API) es **sólo de aplicación**; insertarlo en esas columnas
   violaría el `CHECK`. Las tablas `svc.*` sí lo admiten (son `TEXT` sin `CHECK`,
   con `DEFAULT 'SYNTHETIC_FIXTURE'`).
2. **Tablas sin PK:** `prices` sí la tiene; **no** la tienen `options_chains`,
   `macro_data`, `sentiment`, `market_regimes`, `model_metrics_daily`,
   `system_logs`. En TimescaleDB una hypertable necesita que la columna de tiempo
   forme parte de cualquier PK/UNIQUE, lo que explica el diseño.
3. **Dos fuentes para `svc.*`:** el SQL de 009 y `MIRROR_DDL` en `app/db.py`. Si
   se añade una columna, hay que tocar las dos (o una migración nueva con
   `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`).
4. **`public.alerts` ≠ `svc.alerts`:** la primera es relacional (`user_id UUID`,
   `channel` con `CHECK`, sin `ticker`/`rule_id`); la segunda es un espejo de
   `TEXT` con `ticker` y `rule_id`. La API usa `public` sólo a través de las
   migraciones; en ejecución escribe en `svc.*` cuando hay Postgres.
5. **`007` deja el esquema sin proteger frente al rol administrador.** Los
   `REVOKE` sólo afectan a `argus_app`; los triggers de 009 son la protección
   efectiva.
