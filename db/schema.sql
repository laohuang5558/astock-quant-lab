-- A股量化研究平台 · PostgreSQL 16 + pgvector DDL
-- 目标库：ai-server 192.168.2.203:5432 / ai_db，独立 schema quant
-- 向量维度 512 来自实测 /ai/health：BAAI/bge-small-zh-v1.5 dim=512
-- 设计约束：事实表不可变；修正通过新 run_id 表达；数值只来自 result.json 派生表。
-- 合规：research-only，本模型不含订单路由、资金账户与实盘持仓。

CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS quant;

-- ---------------------------------------------------------------- 主数据
CREATE TABLE IF NOT EXISTS quant.instrument (
    instrument_id   BIGSERIAL PRIMARY KEY,
    code            CHAR(6)      NOT NULL UNIQUE,
    name            TEXT,
    board           TEXT         NOT NULL DEFAULT 'UNKNOWN',
    industry        TEXT         NOT NULL DEFAULT 'UNKNOWN',
    is_st           BOOLEAN      NOT NULL DEFAULT FALSE,
    listed_date     DATE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
COMMENT ON COLUMN quant.instrument.industry IS '缺失填 UNKNOWN，不得猜测';

CREATE TABLE IF NOT EXISTS quant.price_bar (
    instrument_id   BIGINT       NOT NULL REFERENCES quant.instrument(instrument_id),
    trade_date      DATE         NOT NULL,
    price_basis     TEXT         NOT NULL CHECK (price_basis IN ('qfq', 'raw')),
    open            NUMERIC(18,6),
    high            NUMERIC(18,6),
    low             NUMERIC(18,6),
    close           NUMERIC(18,6),
    prev_close      NUMERIC(18,6),
    volume          NUMERIC(20,2),
    amount          NUMERIC(24,2),
    limit_up        NUMERIC(18,6),
    limit_down      NUMERIC(18,6),
    can_buy         BOOLEAN,
    can_sell        BOOLEAN,
    PRIMARY KEY (instrument_id, trade_date, price_basis)
);
CREATE INDEX IF NOT EXISTS ix_price_bar_date ON quant.price_bar (trade_date);
COMMENT ON TABLE quant.price_bar IS '双价格口径：qfq 用于收益/盯市/成交，raw 仅用于涨跌停基准；禁止混算';

CREATE TABLE IF NOT EXISTS quant.factor_definition (
    factor_code     TEXT         PRIMARY KEY,
    formula         TEXT         NOT NULL,
    direction_sign  SMALLINT     NOT NULL DEFAULT 1 CHECK (direction_sign = 1),
    weight          NUMERIC(10,6) NOT NULL,
    enabled         BOOLEAN      NOT NULL DEFAULT TRUE,
    note            TEXT
);
COMMENT ON COLUMN quant.factor_definition.direction_sign IS '预注册方向锁为 +1，禁止据样本反号';

-- ---------------------------------------------------------------- 预注册
CREATE TABLE IF NOT EXISTS quant.preregistration (
    prereg_id       BIGSERIAL PRIMARY KEY,
    hypothesis_hash CHAR(64)    NOT NULL,
    strategy_hash   CHAR(64)    NOT NULL,
    hypothesis_text TEXT        NOT NULL,
    oos_use_count   INTEGER     NOT NULL DEFAULT 1,
    oos_max_uses    INTEGER     NOT NULL DEFAULT 3,
    frozen          BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (hypothesis_hash, strategy_hash)
);
COMMENT ON TABLE quant.preregistration IS '与 state/research_state.json 同步；超过 oos_max_uses 结论降级';

-- ---------------------------------------------------------------- 运行事实
CREATE TABLE IF NOT EXISTS quant.research_run (
    run_id              UUID PRIMARY KEY,
    prereg_id           BIGINT REFERENCES quant.preregistration(prereg_id),
    config_snapshot     JSONB       NOT NULL,
    config_hash         CHAR(64)    NOT NULL,
    metric_fingerprint  CHAR(16)    NOT NULL UNIQUE,
    status              TEXT        NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued','running','succeeded','failed','superseded')),
    stage               TEXT        NOT NULL DEFAULT 'S0_LOCKED',
    git_sha             TEXT,
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    error               TEXT
);
CREATE INDEX IF NOT EXISTS ix_run_created ON quant.research_run (created_at DESC, run_id);
COMMENT ON TABLE quant.research_run IS '不可变事实；一次 result.json 只允许一条运行记录';

CREATE TABLE IF NOT EXISTS quant.run_metric (
    run_id          UUID        NOT NULL REFERENCES quant.research_run(run_id),
    metric_path     TEXT        NOT NULL,
    value_text      TEXT        NOT NULL,
    value_num       NUMERIC(24,8),
    source          TEXT        NOT NULL DEFAULT 'output/result.json',
    PRIMARY KEY (run_id, metric_path)
);
CREATE INDEX IF NOT EXISTS ix_metric_path ON quant.run_metric (metric_path);
COMMENT ON TABLE quant.run_metric IS '只存 METRIC_PATHS 内路径；与 docs/metrics.md 逐项对账';

CREATE TABLE IF NOT EXISTS quant.factor_value (
    run_id          UUID        NOT NULL REFERENCES quant.research_run(run_id),
    trade_date      DATE        NOT NULL,
    code            CHAR(6)     NOT NULL,
    factor_code     TEXT        NOT NULL REFERENCES quant.factor_definition(factor_code),
    raw_value       NUMERIC(24,8),
    zscore          NUMERIC(24,8),
    rank_pct        NUMERIC(10,6),
    PRIMARY KEY (run_id, trade_date, code, factor_code)
);

CREATE TABLE IF NOT EXISTS quant.backtest_trade (
    run_id          UUID        NOT NULL REFERENCES quant.research_run(run_id),
    seq             INTEGER     NOT NULL,
    trade_date      DATE        NOT NULL,
    code            CHAR(6)     NOT NULL,
    side            TEXT        NOT NULL CHECK (side IN ('BUY','SELL')),
    shares          NUMERIC(20,6) NOT NULL,
    price           NUMERIC(18,6) NOT NULL,
    price_basis     TEXT        NOT NULL DEFAULT 'qfq',
    gross           NUMERIC(24,2) NOT NULL,
    commission      NUMERIC(20,2) NOT NULL,
    stamp_tax       NUMERIC(20,2) NOT NULL,
    slippage        NUMERIC(20,2) NOT NULL,
    cash_after      NUMERIC(24,2) NOT NULL CHECK (cash_after >= -0.000001),
    blocked         BOOLEAN     NOT NULL DEFAULT FALSE,
    unfilled        BOOLEAN     NOT NULL DEFAULT FALSE,
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS ix_trade_date ON quant.backtest_trade (run_id, trade_date);
COMMENT ON COLUMN quant.backtest_trade.cash_after IS '纯多头无杠杆硬约束下沉到数据库层；被拒订单登记 blocked/unfilled，不静默丢弃';

CREATE TABLE IF NOT EXISTS quant.equity_point (
    run_id          UUID        NOT NULL REFERENCES quant.research_run(run_id),
    trade_date      DATE        NOT NULL,
    equity          NUMERIC(24,2) NOT NULL,
    cash            NUMERIC(24,2) NOT NULL CHECK (cash >= -0.000001),
    exposure        NUMERIC(12,6) NOT NULL CHECK (exposure <= 1.000001),
    drawdown        NUMERIC(12,6),
    fuse_level      SMALLINT,
    PRIMARY KEY (run_id, trade_date)
);

-- ---------------------------------------------------------------- 质量门
CREATE TABLE IF NOT EXISTS quant.quality_gate_item (
    run_id          UUID        NOT NULL REFERENCES quant.research_run(run_id),
    item_key        TEXT        NOT NULL,
    verdict         TEXT        NOT NULL CHECK (verdict IN ('成立','不成立','存疑')),
    evidence        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    note            TEXT,
    hard_fail       BOOLEAN     NOT NULL DEFAULT FALSE,
    PRIMARY KEY (run_id, item_key)
);
COMMENT ON TABLE quant.quality_gate_item IS '逐条可追溯；裁决由读数与进程内自检推导，禁止硬编码票数';

CREATE TABLE IF NOT EXISTS quant.quality_gate_verdict (
    run_id                      UUID PRIMARY KEY REFERENCES quant.research_run(run_id),
    engineering_gate            TEXT NOT NULL CHECK (engineering_gate IN ('可交付','不可交付')),
    strategy_conclusion_gate    TEXT NOT NULL CHECK (strategy_conclusion_gate IN ('放行','不放行','存疑')),
    final_verdict               TEXT NOT NULL CHECK (final_verdict IN ('放行','不放行','存疑')),
    hard_fail_items             JSONB NOT NULL DEFAULT '[]'::jsonb,
    counts                      JSONB NOT NULL DEFAULT '{}'::jsonb,
    followups                   JSONB NOT NULL DEFAULT '[]'::jsonb,
    computed_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE quant.quality_gate_verdict IS '唯一放行出口；两票分开，禁止互相顶替；无写入接口';

-- ---------------------------------------------------------------- 专家团与编排
CREATE TABLE IF NOT EXISTS quant.audit_opinion (
    run_id          UUID        NOT NULL REFERENCES quant.research_run(run_id),
    panelist        TEXT        NOT NULL,
    attempt         INTEGER     NOT NULL DEFAULT 1,
    verdict         TEXT        NOT NULL CHECK (verdict IN ('成立','不成立','存疑')),
    item            TEXT        NOT NULL,
    evidence_path   TEXT,
    evidence        TEXT,
    severity        TEXT        CHECK (severity IN ('致命','重大','一般')),
    evidence_valid  BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, panelist, attempt, item)
);
COMMENT ON COLUMN quant.audit_opinion.evidence_valid IS 'evidence_path 为空即 false，不计票';

CREATE TABLE IF NOT EXISTS quant.agent_task (
    task_id         UUID PRIMARY KEY,
    run_id          UUID        NOT NULL REFERENCES quant.research_run(run_id),
    stage           TEXT        NOT NULL,
    role            TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued','running','succeeded','failed','skipped')),
    attempt         INTEGER     NOT NULL DEFAULT 1,
    input_hash      CHAR(64),
    output_hash     CHAR(64),
    payload         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    read_only       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, stage, role, attempt)
);
CREATE INDEX IF NOT EXISTS ix_task_queue ON quant.agent_task (status, created_at)
    WHERE status = 'queued';
COMMENT ON TABLE quant.agent_task IS 'PostgreSQL 队列表；worker 用 SELECT ... FOR UPDATE SKIP LOCKED 领取';

CREATE TABLE IF NOT EXISTS quant.agent_message (
    message_id     BIGSERIAL PRIMARY KEY,
    task_id         UUID        NOT NULL REFERENCES quant.agent_task(task_id),
    direction       TEXT        NOT NULL CHECK (direction IN ('dispatch','callback','system')),
    payload         JSONB       NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- 文档向量（只读旁路）
CREATE TABLE IF NOT EXISTS quant.document_chunk (
    chunk_id        BIGSERIAL PRIMARY KEY,
    run_id          UUID REFERENCES quant.research_run(run_id),
    doc_type        TEXT        NOT NULL,
    content         TEXT        NOT NULL,
    embedding       vector(512) NOT NULL,
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    advisory        BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_chunk_embedding ON quant.document_chunk
    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS ix_chunk_scope ON quant.document_chunk (run_id, doc_type);
COMMENT ON COLUMN quant.document_chunk.advisory IS '检索结果仅供参考，质量门忽略该字段';

-- ---------------------------------------------------------------- 交付与验收
CREATE TABLE IF NOT EXISTS quant.delivery_artifact (
    artifact_id     BIGSERIAL PRIMARY KEY,
    run_id          UUID REFERENCES quant.research_run(run_id),
    path            TEXT        NOT NULL,
    sha256          CHAR(64)    NOT NULL,
    size_bytes      BIGINT      NOT NULL,
    storage_url     TEXT,
    verify_status   TEXT        NOT NULL DEFAULT 'pending'
                    CHECK (verify_status IN ('pending','verified','failed')),
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_artifact_run ON quant.delivery_artifact (run_id);

-- ---------------------------------------------------------------- 对账视图
CREATE OR REPLACE VIEW quant.v_run_summary AS
SELECT r.run_id,
       r.metric_fingerprint,
       r.status,
       r.stage,
       r.git_sha,
       v.final_verdict,
       v.engineering_gate,
       v.strategy_conclusion_gate,
       (SELECT count(*) FROM quant.run_metric m WHERE m.run_id = r.run_id) AS metric_count,
       (SELECT count(*) FROM quant.quality_gate_item g WHERE g.run_id = r.run_id AND g.hard_fail) AS hard_fail_count,
       r.created_at
FROM quant.research_run r
LEFT JOIN quant.quality_gate_verdict v ON v.run_id = r.run_id;
COMMENT ON VIEW quant.v_run_summary IS '验收总览：指纹、裁决、指标条数、硬否决议数';
