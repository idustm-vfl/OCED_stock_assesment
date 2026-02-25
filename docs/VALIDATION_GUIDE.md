# OCED Picker Validation Guide

This document explains the validation requirements and how to verify that the OCED picker is working correctly.

## Quick Validation

Run the validation script to check all requirements:

```bash
python validate_picker.py
```

This will:
1. Print masked Massive API keys
2. Validate all picks in the `weekly_picks` table
3. Check for placeholder math, null values, and missing provenance
4. Display entries in `weekly_pick_missing` table
5. Exit with code 0 (success) or 1 (failure)

## Requirements

### Absolute Rules (Data Validity Gate)

NO row may appear in `weekly_picks` unless ALL of the following are true:

1. ✅ `strike IS NOT NULL`
2. ✅ `call_bid IS NOT NULL`
3. ✅ `call_ask IS NOT NULL`
4. ✅ `call_mid > 0`
5. ✅ `premium_100 > 0` AND equals `call_mid * 100` (within 0.01 tolerance)
6. ✅ `premium_yield > 0` AND equals `premium_100 / pack_100_cost` (within 0.0001 tolerance)
7. ✅ `price_source` is not empty
8. ✅ `chain_source` is not empty
9. ✅ `premium_source` is not empty
10. ✅ `strike_source` is not empty

### Banned Invalid States

These conditions are **FATAL ERRORS** and must write to `audit_math` table:

- ❌ `premium_100 == price` (underlying stock price) - **PLACEHOLDER MATH**
- ❌ `premium_yield` is a constant placeholder (e.g., `0.010`)
- ❌ `strike` is blank/null/N/A
- ❌ `call_mid` is null or <= 0
- ❌ Any `*_source` field is empty string

## Running Tests

### Unit Tests

```bash
# Run all picker validation tests
MASSIVE_API_KEY=test_key pytest tests/test_picker_validation.py -v

# Run all report tests
MASSIVE_API_KEY=test_key pytest tests/test_reports.py -v

# Run all tests
MASSIVE_API_KEY=test_key pytest tests/ -v
```

### Integration Tests

```bash
# Initialize database
python -m massive_tracker.cli init

# Run picker
python -m massive_tracker.cli picker --top-n 20

# Validate results
python validate_picker.py

# Generate Monday report
python -m massive_tracker.cli monday --seed 9300 --lane SAFE_HIGH --top-n 10

# Generate Friday scorecard
python -m massive_tracker.cli friday_close
```

## Validation Script Output

### Success Example

```
================================================================================
OCED Picker Validation
================================================================================

Runtime Keys:
MASSIVE_API_KEY: abc12*****
MASSIVE_KEY_ID: None
MASSIVE_SECRET_KEY: qwe45*****

Validating 15 picks from weekly_picks table...

================================================================================
✅ ALL VALIDATION CHECKS PASSED

   Total picks validated: 15
   All picks have:
   - Non-null strike, bid, ask, mid
   - Valid premium_100 and premium_yield (> 0)
   - Correct math (premium_100 = mid*100, yield = prem/cost)
   - No placeholder math (premium_100 ≠ price)
   - Populated source fields

================================================================================
✅ Validation successful - All requirements met!
================================================================================
```

### Failure Example

```
================================================================================
❌ VALIDATION FAILURES DETECTED

   Total picks: 15
   Failures: 3

   ❌ AAPL: BANNED placeholder math - premium_100 (150.0) == price (150.0)
   ❌ MSFT: strike is NULL
   ❌ GOOG: price_source is empty

================================================================================
❌ Validation failed - See errors above
================================================================================
```

## Checking the Database

### Query Valid Picks

```sql
SELECT ticker, price, strike, call_mid, premium_100, premium_yield,
       price_source, chain_source, premium_source, strike_source
FROM weekly_picks
WHERE strike IS NOT NULL
  AND call_bid IS NOT NULL
  AND call_ask IS NOT NULL
  AND call_mid > 0
  AND premium_100 > 0
  AND premium_yield > 0
ORDER BY premium_yield DESC;
```

### Query Missing/Failed Picks

```sql
SELECT ticker, stage, reason, detail, source
FROM weekly_pick_missing
ORDER BY ts DESC
LIMIT 20;
```

### Query Audit Math Failures

```sql
SELECT ts, ticker, stage, field, expected, actual, source_ref
FROM audit_math
WHERE ok = 0
ORDER BY ts DESC
LIMIT 20;
```

## What Gets Logged

### weekly_picks Table
- Only VALID picks that pass all validation checks
- Invalid picks are NOT written (changed from previous behavior)

### weekly_pick_missing Table
- Tickers that failed any validation check
- Includes stage (price/chain/premium/selection/provenance)
- Includes reason and detail for debugging

### audit_math Table
- All validation failures
- Includes expected vs actual values
- Includes source reference for traceability

## Troubleshooting

### No picks in weekly_picks table

1. Check if universe is populated:
   ```bash
   python -m massive_tracker.cli init
   ```

2. Check if prices are cached:
   ```bash
   python -m massive_tracker.cli stream --tickers SPY,QQQ
   # Let it run for 2-3 minutes
   ```

3. Check missing picks table:
   ```sql
   SELECT * FROM weekly_pick_missing ORDER BY ts DESC LIMIT 10;
   ```

### Picks fail validation

1. Check audit_math table for specific failures:
   ```sql
   SELECT * FROM audit_math WHERE ok = 0 ORDER BY ts DESC LIMIT 10;
   ```

2. Check source fields are populated:
   ```sql
   SELECT ticker, price_source, chain_source, premium_source, strike_source
   FROM weekly_picks;
   ```

3. Verify Massive API keys are set:
   ```bash
   python -m massive_tracker.cli env_check
   ```

### Math validation failures

Check that premium calculations are correct:

```sql
SELECT 
    ticker,
    call_mid,
    premium_100,
    (call_mid * 100) as expected_prem,
    abs(premium_100 - (call_mid * 100)) as prem_error,
    pack_100_cost,
    premium_yield,
    (premium_100 / pack_100_cost) as expected_yield,
    abs(premium_yield - (premium_100 / pack_100_cost)) as yield_error
FROM weekly_picks
WHERE abs(premium_100 - (call_mid * 100)) >= 0.01
   OR abs(premium_yield - (premium_100 / pack_100_cost)) >= 0.0001;
```

## Report Generation

### Monday Report

```bash
python -m massive_tracker.cli monday --seed 9300 --lane SAFE_HIGH --top-n 10
```

Output: `data/reports/monday_run_YYYY-MM-DD.md`

Sections:
- Universe Health
- LLM Picks (Top 5 Safest, Top 5 Premium)
- OCED Table
- Best Contract Candidates (grouped by seed bucket: <=5k, <=10k, <=25k, <=50k, >50k)
- Promotions
- End-of-Week Scoreboard

### Friday Scorecard

```bash
python -m massive_tracker.cli friday_close
```

Output: `data/reports/weekly_scorecard_YYYY-MM-DD.md`

Sections:
1. Predicted vs Realized (top 5 with prediction error & rank drift)
2. LLM Hit Rate (% positive PnL)
3. Strike Quality (% assigned vs % expired OTM)
4. Prediction Error Distribution (mean, median)
5. Rank Drift Analysis (Monday rank vs Friday PnL rank)
6. Full Outcomes table

## CI/CD Integration

The validation script returns appropriate exit codes for CI:

```bash
#!/bin/bash
set -e

# Run picker
python -m massive_tracker.cli picker --top-n 20

# Validate (fails if validation fails)
python validate_picker.py

# Generate reports if validation passed
python -m massive_tracker.cli monday --seed 9300 --lane SAFE_HIGH --top-n 10
```

Exit codes:
- `0`: All validation checks passed
- `1`: One or more validation failures detected

## References

- Original issue: "OCED Tracker: Fix Placeholder Math, Enforce Massive-Only Provenance"
- Code changes in PR: `copilot/fix-placeholder-math-provenance`
- Key files:
  - `massive_tracker/picker.py` - Validation logic
  - `massive_tracker/store.py` - Database schema
  - `massive_tracker/config.py` - Key management
  - `validate_picker.py` - Validation script
  - `tests/test_picker_validation.py` - Unit tests
