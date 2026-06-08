-- description: OBS-003 — eval_runs (1 ligne agrégée/run live) + index partiel live-latest
CREATE TABLE eval_runs (
    id                 UUID PRIMARY KEY,
    git_sha            TEXT NOT NULL,
    runner_mode        TEXT NOT NULL CHECK (runner_mode = 'live'),
    golden_set_version TEXT NOT NULL,
    faithfulness       NUMERIC(5,4) NOT NULL,
    answer_relevancy   NUMERIC(5,4) NOT NULL,
    context_precision  NUMERIC(5,4) NOT NULL,
    context_recall     NUMERIC(5,4) NOT NULL,
    entries_total      INT NOT NULL,
    entries_ok         INT NOT NULL,
    entries_errors     INT NOT NULL,
    started_at         TIMESTAMPTZ NOT NULL,
    finished_at        TIMESTAMPTZ NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX eval_runs_live_latest_idx
    ON eval_runs (finished_at DESC)
    WHERE runner_mode = 'live';
