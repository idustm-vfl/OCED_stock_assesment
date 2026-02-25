# Massive Access Log

## Status
- MCP server wrapper script removed; using Python client + REST directly.
- massive Python SDK present: version 2.0.2.
- MASSIVE_API_KEY is the REST token; S3 uses MASSIVE_KEY_ID + MASSIVE_SECRET_KEY.
-

## Environment keys in workspace
- MASSIVE_API_KEY (REST)
- MASSIVE_KEY_ID (fallback if needed)
- MASSIVE_SECRET_KEY
- MASSIVE_S3_ENDPOINT
- MASSIVE_S3_BUCKET
- MASSIVE_STOCKS_PREFIX
- MASSIVE_OPTIONS_PREFIX

## MCP tool surface (v0.6.0)
53 tools exposed via mcp_massive FastMCP (grouped roughly by domain):
- Core market data: get_aggs, list_aggs, get_grouped_daily_aggs, get_daily_open_close_agg, get_previous_close_agg
- Trades/quotes/currency: list_trades, get_last_trade, get_last_crypto_trade, list_quotes, get_last_quote, get_last_forex_quote, get_real_time_currency_conversion
- Snapshots: list_universal_snapshots, get_snapshot_all, get_snapshot_direction, get_snapshot_ticker, get_snapshot_option, get_snapshot_crypto_book
- Market status/meta: get_market_holidays, get_market_status, list_tickers, get_ticker_details, get_ticker_types, list_conditions, get_exchanges
- Corporate actions/news: list_splits, list_dividends, list_ticker_news
- Fundamentals: list_stock_financials, list_ipos
- Short/treasury/macro: list_short_interest, list_short_volume, list_treasury_yields, list_inflation
- Benzinga set: list_benzinga_analyst_insights, list_benzinga_analysts, list_benzinga_consensus_ratings, list_benzinga_earnings, list_benzinga_firms, list_benzinga_guidance, list_benzinga_news, list_benzinga_ratings
- Futures: list_futures_aggregates, list_futures_contracts, get_futures_contract_details, list_futures_products, get_futures_product_details, list_futures_quotes, list_futures_trades, list_futures_schedules, list_futures_schedules_by_product_code, list_futures_market_statuses, get_futures_snapshot

## Notes
- Use MASSIVE_API_KEY for all REST/SDK calls; no fallback to S3 keys.
- Real-time endpoints (snapshots/last trade) are not authorized with current plan; list-oriented endpoints like `list_tickers` work.
- For S3 flatfile access, ensure the S3 keys above remain set; those are used by ingest in this repo.
- Re-run smoke tests:
	- SDK tickers: `python - <<'PY' ... RESTClient(...).list_tickers(limit=1)`
	- Real-time snapshot (expected 403 until upgraded): `RESTClient(...).get_snapshot_ticker('stocks', 'AAPL')`
