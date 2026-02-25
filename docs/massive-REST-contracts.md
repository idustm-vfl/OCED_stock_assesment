# REST
## Options

### All Contracts

**Endpoint:** `GET /v3/reference/options/contracts`

**Description:**

Retrieve a comprehensive index of options contracts, encompassing both active and expired listings. This endpoint can return a broad selection of contracts or be narrowed down to those tied to a specific underlying ticker. Each contract entry includes details such as contract type (call/put), exercise style, expiration date, and strike price. By exploring this index, users can assess market availability, analyze contract characteristics, and refine their options trading or research strategies.

Use Cases: Market availability analysis, strategy development, research and modeling, contract exploration.

## Query Parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `underlying_ticker` | string | No | Query for contracts relating to an underlying stock ticker. |
| `ticker` | string | No | This parameter has been deprecated. To search by specific options ticker, use the Options Contract endpoint [here](https://massive.com/docs/rest/options/contracts/contract-overview). |
| `contract_type` | string | No | Query by the type of contract. |
| `expiration_date` | string | No | Query by contract expiration with date format YYYY-MM-DD. |
| `as_of` | string | No | Specify a point in time for contracts as of this date with format YYYY-MM-DD. Defaults to today's date. |
| `strike_price` | number | No | Query by strike price of a contract. |
| `expired` | boolean | No | Query for expired contracts. Default is false. |
| `underlying_ticker.gte` | string | No | Range by underlying_ticker. |
| `underlying_ticker.gt` | string | No | Range by underlying_ticker. |
| `underlying_ticker.lte` | string | No | Range by underlying_ticker. |
| `underlying_ticker.lt` | string | No | Range by underlying_ticker. |
| `expiration_date.gte` | string | No | Range by expiration_date. |
| `expiration_date.gt` | string | No | Range by expiration_date. |
| `expiration_date.lte` | string | No | Range by expiration_date. |
| `expiration_date.lt` | string | No | Range by expiration_date. |
| `strike_price.gte` | number | No | Range by strike_price. |
| `strike_price.gt` | number | No | Range by strike_price. |
| `strike_price.lte` | number | No | Range by strike_price. |
| `strike_price.lt` | number | No | Range by strike_price. |
| `order` | string | No | Order results based on the `sort` field. |
| `limit` | integer | No | Limit the number of results returned, default is 10 and max is 1000. |
| `sort` | string | No | Sort field used for ordering. |

## Response Attributes

| Field | Type | Description |
| --- | --- | --- |
| `next_url` | string | If present, this value can be used to fetch the next page of data. |
| `request_id` | string | A request id assigned by the server. |
| `results` | array[object] | An array of results containing the requested data. |
| `results[].additional_underlyings` | array[object] | If an option contract has additional underlyings or deliverables associated with it, they will appear here. See <a rel="noopener noreferrer nofollow" target="_blank" href="https://www.optionseducation.org/referencelibrary/faq/splits-mergers-spinoffs-bankruptcies">here</a> for some examples of what might cause a contract to have additional underlyings. |
| `results[].cfi` | string | The 6 letter CFI code of the contract (defined in <a rel="nofollow" target="_blank" href="https://en.wikipedia.org/wiki/ISO_10962">ISO 10962</a>) |
| `results[].contract_type` | string | The type of contract. Can be "put", "call", or in some rare cases, "other". |
| `results[].correction` | integer | The correction number for this option contract. |
| `results[].exercise_style` | enum: american, european, bermudan | The exercise style of this contract. See <a rel="nofollow" target="_blank" href="https://en.wikipedia.org/wiki/Option_style">this link</a> for more details on exercise styles. |
| `results[].expiration_date` | string | The contract's expiration date in YYYY-MM-DD format. |
| `results[].primary_exchange` | string | The MIC code of the primary exchange that this contract is listed on. |
| `results[].shares_per_contract` | number | The number of shares per contract for this contract. |
| `results[].strike_price` | number | The strike price of the option contract. |
| `results[].ticker` | string | The ticker for the option contract. |
| `results[].underlying_ticker` | string | The underlying ticker that the option contract relates to. |
| `status` | string | The status of this request's response. |

## Sample Response

```json

## Flatfile download example (S3)

Use the Massive flatfile credentials, not the REST `MASSIVE_API_KEY`:

```python
import os
import boto3

session = boto3.Session(
  aws_access_key_id=os.environ["MASSIVE_KEY_ID"],
  aws_secret_access_key=os.environ["MASSIVE_SECRET_KEY"],
)

{
bucket = "flatfiles"
object_key = "us_options_opra/day_aggs_v1/2025/12/2025-12-18.csv.gz"
s3.download_file(bucket, object_key, "/tmp/2025-12-18.csv.gz")
```
  "request_id": "603902c0-a5a5-406f-bd08-f030f92418fa",
  "results": [
    {
      "cfi": "OCASPS",
      "contract_type": "call",
      "exercise_style": "american",
      "expiration_date": "2021-11-19",
      "primary_exchange": "BATO",
      "shares_per_contract": 100,
      "strike_price": 85,
      "ticker": "O:AAPL211119C00085000",
      "underlying_ticker": "AAPL"
    },
    {
      "additional_underlyings": [
        {
          "amount": 44,
          "type": "equity",
          "underlying": "VMW"
        },
        {
          "amount": 6.53,
          "type": "currency",
          "underlying": "USD"
        }
      ],
      "cfi": "OCASPS",
      "contract_type": "call",
      "exercise_style": "american",
      "expiration_date": "2021-11-19",
      "primary_exchange": "BATO",
      "shares_per_contract": 100,
      "strike_price": 90,
      "ticker": "O:AAPL211119C00090000",
      "underlying_ticker": "AAPL"
    }
  ],
  "status": "OK"
}
```Paste the block below into Copilot as an **append-only continuation** to the “leftovers” message. It is written as **direct implementation instructions** with exact acceptance criteria.

---

## Copilot: Continue from partial progress — implement remaining items exactly

### Priority 1 — Summary rewrite (DB-first, buckets, promotions, universe status)

**File:** `massive_tracker/summary.py`

**Goal:** Generate `data/reports/summary.md` that always shows the full universe + weekly picks grouped by seed buckets, plus promotions and contract health. No JSONL dependency.

#### Implement `write_summary(db_path: str="data/sqlite/tracker.db", seed: float=9300.0) -> str`

Writes markdown to `data/reports/summary.md` and returns the markdown string.

#### Sections required (in this order)

1. **Header**

* Generated timestamp (UTC)
* DB path
* Seed used

2. **Universe Status**

* Total tickers in universe (enabled)
* Tickers missing price (market_last missing) — list them
* Count of tickers with sufficient 1m bars (>=120 and >=390)

3. **Weekly Picks (Grouped by Pack Cost Buckets)**
   Buckets are hard-coded:

* `<= 5k`
* `<= 10k`
* `<= 25k`
* `<= 50k`
* `> 50k`

For each bucket show a table with columns (minimum):

* ticker
* category
* price
* pack_100_cost
* lane
* expiry
* strike
* est_weekly_prem_100 (or blank)
* premium_yield
* bars_1m_count
* fft_status
* fractal_status
* rank_score

Rules:

* Pull from weekly picks table (whatever name already added). If none exists, show a clear “No weekly picks computed yet. Run picker.”
* Sort within bucket by `rank_score DESC` (or `premium_yield DESC` if you implemented that as primary)
* Display at least top 10 per bucket, but include counts.

4. **Top 5 Safest**

* Filter weekly picks to lane == SAFE (or safest lane mapping)
* Top 5 by `rank_score DESC`
  Show concise list: ticker, price, pack_cost, strike, expiry, premium_yield.

5. **Top 5 Premium Leaders**

* Only where premium_yield not null
* Top 5 by premium_yield DESC

6. **Promoted This Run**

* Query promotions table for latest date/time window:

  * simplest: last 24h or last N rows (20)
* Show table: ts, ticker, expiry, strike, lane, seed, decision, reason

7. **Active Contract Health**

* Read open positions from `option_positions`
* For each show: ticker, expiry, strike, right, qty, last stock price, call_mid/bid/ask if available, recommendation/alert if stored, and gate flags if available.

8. **Side-by-Side Decision Changes (stub initially, but section must exist)**

* If `compare_models` output exists in DB or file, show it.
* If not implemented yet, print:
  “Side-by-side compare not generated yet. Run `python -m massive_tracker.cli compare`.”

**Acceptance:** Running `python -m massive_tracker.cli summary` produces a readable markdown with all 8 sections and at least one bucket table even if empty.

---

### Priority 2 — Add compare_models module (minimal but real)

**File:** `massive_tracker/compare_models.py`

Implement:
`run_compare(db_path: str, seed: float=9300.0, top_n: int=10) -> dict`

Compare 3 variants for the same weekly pick set:

1. **baseline**: ignores structure gates entirely (bars_1m_count, fft/fractal status do not affect)
2. **gated**: applies structure gates (if bars_1m_count >= 120 then require entropy/roughness not “unstable”; if insufficient history do not block)
3. **weighted**: applies structure as penalty rather than hard reject

Output:

* A list of tickers whose promotion decision changes between models
* A list of tickers whose strike changes between models (if your strike picker has multiple candidates)

Store results:

* Either in DB table `model_compares` or in `data/reports/model_compare.json`

Then summary.py must read and render this into section 8.

**Acceptance:** `python -m massive_tracker.cli compare` produces non-empty JSON and summary prints the section (even if no changes detected).

---

### Priority 3 — CLI wiring (one-command usability)

**File:** `massive_tracker/cli.py`

Add commands (if missing):

* `summary --db-path ... --seed 9300`

  * calls `write_summary(...)`
* `promote --seed 9300 --lane SAFE_HIGH --top-n 3`

  * calls promotion logic and prints promoted tickers + reasons
* `compare --seed 9300 --top-n 10`

  * calls `run_compare` and prints path to saved output
* `daily --seed 9300 --lane SAFE_HIGH --top-n 3`

  * runs: sync_universe → picker → promote → monitor → summary
  * prints single “Done → data/reports/summary.md”

**Acceptance:** End user only needs:
`python -m massive_tracker.cli daily`

---

### Priority 4 — UI additions (Streamlit buttons)

**File:** `ui_app.py` (or your streamlit entry)

Add buttons and wire to the same functions (no shelling out to CLI):

Buttons:

* Sync Universe
* Run Picker
* Promote (inputs: seed, lane, top_n)
* Run Monitor
* Generate Summary
* Run Compare
* Show Promotions Log (table)
* Add ticker (text input + category optional)

Display:

* Latest summary markdown rendered in app (read `data/reports/summary.md`)
* Universe table with enabled flag and last price

**Acceptance:** UI supports full workflow without remembering CLI commands.

---

### Priority 5 — Sync on startup

Ensure these are called automatically:

* On cli init/run/daily and on UI startup:

  * `sync_universe(DB(db_path))`
* Ensure universe table contains all tickers from universe.py every time.

---

### Notes / constraints

* Do not reintroduce JSONL as a dependency for summary.
* Keep config loads centralized (CFG only; no new getenv calls in runtime modules).

---

This closes the remaining gaps and makes the system usable: full universe shown, bucketed picks, promotion decisions visible, and side-by-side proof for FFT/fractal impact.
