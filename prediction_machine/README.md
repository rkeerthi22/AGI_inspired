# Prediction Machine — Self-Improving Prediction System

## Purpose

The Prediction Machine is a **self-improving prediction system** that makes
verifiable predictions about its own operations, records the actual outcomes,
measures prediction accuracy, and iteratively improves its models based on
the gap between predicted and actual results.

Every prediction is **immutable** — once written, the predicted value, model
version, confidence, and input features can never be modified. Only the
outcome-related fields (actual result, error metrics, validity) may be
updated, and the actual outcome may be recorded **exactly once**.

This discipline prevents the system from retroactively "improving" its
predictions after the outcome is already known — the prediction must stand
on its own merits, and the error is measured honestly.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     DAILY LOOP (run_daily.py)                 │
│                                                               │
│  1. PREDICT   →  2. WAIT   →  3. COLLECT   →  4. EVALUATE    │
│     (predictors)     (time)    (collectors)   (evaluator)    │
│                                                     │         │
│  5. IMPROVE  ◄──────────────────────────────────────┘         │
│     (experiments, model version upgrades)                      │
└─────────────────────────────────────────────────────────────┘
         │                                       ▲
         ▼                                       │
┌─────────────────┐                     ┌──────────────────┐
│  PredictionStore │                     │   Predictors     │
│  (SQLite,        │◄───────────────────│  task_outcome    │
│   immutable)     │    read/create      │  video_engagement│
│                  │                     │  skill_safety    │
│  predictions     │──── read/create ───►│  miks_campaign   │
│  experiments     │                     └──────────────────┘
│  model_versions  │
│                  │                     ┌──────────────────┐
│                  │──── read/update ───►│   Collectors     │
│                  │                     │  task_outcome    │
└──────────────────┘                     │  video_engagement│
         │                               │  skill_safety    │
         ▼                               │  miks_campaign   │
┌─────────────────┐                      └──────────────────┘
│  Evaluator      │
│  compute_error  │                     ┌──────────────────┐
│  evaluate()     │──── report ────────►│  Experiments     │
│  calibration    │                     │  (improvements)  │
│  bias           │                      └──────────────────┘
└─────────────────┘
```

### Core Components

| Component | Location | Responsibility |
|-----------|----------|---------------|
| **PredictionStore** | `core/prediction_store.py` | Immutable SQLite-backed prediction database. Enforces write-once outcomes, anti-cheat timestamp ordering, model version registration, and experiment tracking. |
| **Predictors** | `predictors/` | Four prediction modules: `task_outcome`, `video_engagement`, `skill_safety`, `miks_campaign`. Each produces a prediction payload with a confidence level and outcome-due timestamp. |
| **Collectors** | `collectors/` | Four collector modules that fetch actual outcomes from their respective data sources (ledger.db, YouTube API, canary deployment, MIKS campaign data). |
| **Evaluator** | `evaluation/evaluator.py` | Computes per-prediction error metrics (`compute_error`) and aggregate accuracy/calibration/bias statistics (`PredictionEvaluator.evaluate`). |
| **Daily Loop** | `run_daily.py` | Orchestrates the daily cycle: predict → collect → evaluate → improve. |
| **Integrations** | `integrations/` | Hooks into the batch_runner and other orchestrator components for automatic prediction and outcome collection. |

## Folder Structure

```
prediction_machine/
├── __init__.py
├── README.md
├── config/
│   └── default_config.yaml          # Database paths, model versions, windows, anti-cheat settings
├── core/
│   ├── __init__.py
│   └── prediction_store.py          # PredictionStore — immutable SQLite store
├── predictors/
│   ├── __init__.py
│   ├── task_outcome/
│   │   └── __init__.py               # Predicts task pass/fail + token usage
│   ├── video_engagement/
│   │   └── __init__.py               # Predicts video view counts (24h/3d/7d)
│   ├── skill_safety/
│   │   └── __init__.py               # Predicts skill regression risk (canary)
│   └── miks_campaign/
│       └── __init__.py               # Predicts MIKS campaign views/engagement/revenue
├── collectors/
│   ├── __init__.py
│   ├── task_outcome_collector.py     # Collects actual task outcomes from ledger.db
│   ├── video_engagement_collector.py # Collects actual video views from YouTube API
│   ├── skill_safety_collector.py     # Collects actual canary regression results
│   └── miks_campaign_collector.py    # Collects actual MIKS campaign metrics
├── evaluation/
│   ├── __init__.py
│   └── evaluator.py                  # compute_error() + PredictionEvaluator
├── integrations/
│   └── (batch_runner hooks)
├── run_daily.py                       # Daily loop runner
├── data/
│   └── predictions.db                # SQLite database (production)
├── reports/
│   └── daily/                        # Daily evaluation reports
└── tests/
    ├── __init__.py
    ├── test_prediction_store.py      # PredictionStore tests (temp DB)
    ├── test_evaluator.py             # Evaluator tests
    ├── test_anti_cheat.py            # Anti-cheating rule tests
    └── run_tests.py                  # Test runner
```

## Daily Loop

The daily loop (`run_daily.py`) runs the following cycle each day:

### 1. Predict
For each prediction type, the active model version generates predictions
for all pending targets:

- **task_outcome**: Before a task runs, predict verdict (pass/fail) and
  token usage.
- **video_engagement**: When a video is published, predict 24h/3d/7d view
  counts.
- **skill_safety**: Before a skill update is deployed, predict regression
  risk (high/low).
- **miks_campaign**: When a campaign launches, predict total views,
  engagement, and revenue over the 7-day window.

Each prediction is written to the PredictionStore with:
- `prediction_type`, `target`, `prediction` (JSON payload), `confidence`
  (low/medium/high), `input_features` (JSON), `model_version`,
  `outcome_due_at` (ISO timestamp), `code_commit`.

### 2. Wait
Predictions mature as their `outcome_due_at` timestamps pass.

### 3. Collect Outcomes
For each prediction whose `outcome_due_at` has passed and whose outcome
hasn't been recorded, the corresponding collector fetches the actual result
from the data source and calls `record_outcome()`.

### 4. Evaluate
The evaluator computes:
- Per-prediction error metrics via `compute_error(type, prediction, actual)`.
- Aggregate statistics via `PredictionEvaluator().evaluate(mature_rows)`:
  - Verdict/directional accuracy
  - Mean/median/min/max percentage error
  - Calibration by confidence level (high/medium/low)
  - Bias (signed error — positive = overprediction)
  - Per-version breakdown
  - Sample size warnings (n < 10)

### 5. Improve
Based on the evaluation report:
- Create experiments to test hypotheses about model improvements.
- Run backtests with the proposed change.
- If the new model outperforms the previous one (decision = ACCEPT),
  register and activate the new model version.
- Log the experiment with `previous_metric`, `new_metric`, `sample_size`,
  and `backtest_details`.

## Anti-Cheating Rules

The system enforces several anti-cheating rules to prevent the prediction
machine from gaming its own metrics:

### 1. Prediction Immutability
Once a prediction is created, the following fields **can never be modified**:
- `prediction_id`, `prediction_type`, `created_at`, `target`
- `prediction` (the predicted value), `confidence`, `input_features`
- `model_version`, `code_commit`, `outcome_due_at`

Only outcome-related fields may be updated, and only via `record_outcome()`
or `invalidate_prediction()`.

### 2. Write-Once Outcomes
`record_outcome()` can be called **exactly once** per prediction. A second
call raises `ValueError`. This prevents updating the actual after seeing
the error.

### 3. Timestamp Ordering
When recording an outcome, the store verifies that
`prediction.created_at <= actual_recorded_at`. If the prediction's
`created_at` is after the recording time (i.e., the prediction was
back-dated after the outcome was known), `record_outcome()` raises
`ValueError` with an "Anti-cheat violation" message.

### 4. Circular Validation Detection
A prediction whose `actual_source` indicates it was validated by the same
run that made the prediction (e.g., `actual_source = "self_validated"`)
should be flagged and invalidated. The store provides `invalidate_prediction()`
for this purpose.

### 5. Fabricated Actual Detection
An outcome recorded with an empty or unreliable `actual_source` (e.g.,
`""`, `"manual"`, `"guess"`) should be flagged as fabricated and
invalidated via `invalidate_prediction()`.

### 6. Duplicate Predictions
The same target **may** have multiple predictions (e.g., from different
model versions), but each prediction must have a unique `prediction_id`
(UUID). This allows A/B comparisons between model versions on the same
target.

## Improvement Rules

The system improves through a disciplined experiment cycle:

1. **Observe a failure**: The evaluation report shows that a model
   underperforms on a specific metric (e.g., high token error, low
   directional accuracy, systematic bias).

2. **Form a hypothesis**: Identify the likely cause (e.g., "training data
   biased toward successful tasks").

3. **Propose a change**: Describe the fix (e.g., "add failure examples to
   training set").

4. **Backtest**: Run the proposed change against historical mature
   predictions and measure the new metric.

5. **Decide**:
   - **ACCEPT**: The new model outperforms → register and activate the new
     model version.
   - **REJECT**: The new model doesn't improve → keep the current version.
   - **PENDING**: More data needed → wait for more mature predictions.

6. **Record**: Log the experiment with `previous_metric`, `new_metric`,
   `sample_size`, `decision`, and `backtest_details`.

### Improvement Constraints
- **Minimum sample size**: Metrics from fewer than 10 predictions are
  flagged with `sample_size_warning = True` and a note that they are not
  reliable.
- **Version lineage**: Each new model version records its
  `parent_version`, so the improvement lineage is traceable.
- **Only one active version per type**: `activate_model_version()`
  deactivates all other versions for the same prediction type.

## Success Definition

The prediction machine is successful when:

1. **Accuracy improves over time**: Mean percentage error decreases
   across model versions for each prediction type.

2. **Calibration is honest**: Predictions marked "high confidence" are
   correct more often than "medium", which are correct more often than
   "low". The calibration buckets reflect real reliability.

3. **Bias is low**: The signed error (bias) approaches zero — the system
   doesn't systematically over- or under-predict.

4. **Sample sizes grow**: The number of mature predictions per type
   exceeds the minimum threshold (n ≥ 10) so metrics are reliable.

5. **Experiments lead to improvements**: ACCEPTED experiments result in
   measurably better model versions, and the lineage of improvements is
   traceable through `parent_version` chains.

6. **No cheating**: All predictions are immutable, outcomes are
   write-once, timestamps are ordered, and no circular or fabricated
   validations exist in the training data.

## Phases

### Phase 1: Instrument
**Goal**: Wire prediction creation into existing workflows so every
significant action generates a prediction before it executes.

- Implement the four predictors (task_outcome, video_engagement,
  skill_safety, miks_campaign).
- Implement the PredictionStore with immutability and anti-cheat rules.
- Hook predictors into the batch_runner, video publishing, skill updates,
  and campaign launches.
- Use placeholder model versions (v1) — accuracy doesn't matter yet,
  only that predictions are being created and stored.

### Phase 2: Accumulate
**Goal**: Let predictions mature and collect actual outcomes.

- Implement the four collectors to fetch real outcomes from their data
  sources.
- Run the daily loop to collect outcomes for past-due predictions.
- Accumulate mature prediction/outcome pairs in the store.
- Do not change models yet — just gather data.

### Phase 3: Baselines
**Goal**: Evaluate the accumulated data and establish baseline metrics.

- Implement the evaluator with `compute_error()` and
  `PredictionEvaluator.evaluate()`.
- Generate the first evaluation report per prediction type and model
  version.
- Identify systematic failures (bias, low calibration, high error).
- Register baseline metrics as the `previous_metric` for future
  experiments.

### Phase 4: Improve
**Goal**: Run experiments to improve model accuracy.

- For each observed failure, create an experiment with a hypothesis and
  proposed change.
- Backtest the proposed change against mature predictions.
- ACCEPT improvements and activate new model versions.
- Track the improvement lineage through `parent_version`.
- Re-evaluate after each accepted change to measure progress.

### Phase 5: Advanced
**Goal**: Move beyond manual improvement to automated optimization.

- Automated experiment proposal: the system identifies failures and
  suggests hypotheses without human intervention.
- Online learning: update models incrementally as new outcomes arrive,
  not just in batch experiments.
- Cross-type transfer: insights from one prediction type (e.g.,
  task_outcome) inform models for another (e.g., skill_safety).
- Uncertainty quantification: replace coarse confidence levels
  (low/medium/high) with calibrated probability distributions.
- Causal inference: distinguish correlation from causation in
  improvement experiments.