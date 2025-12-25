from massive_tracker.store import DB
import os

db = DB("data/sqlite/tracker.db")
print(f"DB exists: {os.path.exists(db.path)}")

with db.connect() as con:
    universe = con.execute("SELECT count(*) FROM universe").fetchone()[0]
    oced = con.execute("SELECT count(*) FROM oced_scores").fetchone()[0]
    picks = con.execute("SELECT count(*) FROM weekly_picks").fetchone()[0]
    health = con.execute("SELECT count(*) FROM option_features").fetchone()[0]
    prices = con.execute("SELECT count(*) FROM market_last").fetchone()[0]
    contracts = con.execute("SELECT count(*) FROM options_contracts").fetchone()[0]
    
print(f"Universe: {universe}")
print(f"OCED Scores: {oced}")
print(f"Weekly Picks: {picks}")
print(f"Contract Health: {health}")
print(f"Market Last: {prices}")
print(f"Options Contracts: {contracts}")

if oced > 0:
    print("\nSample OCED Scores (Latest):")
    rows = con.execute("SELECT ticker, ts, CoveredCall_Suitability FROM oced_scores ORDER BY ts DESC LIMIT 5").fetchall()
    for r in rows:
        print(f"  {r}")

if prices > 0:
    print("\nSample Prices:")
    rows = con.execute("SELECT ticker, price, ts FROM market_last LIMIT 5").fetchall()
    for r in rows:
        print(f"  {r}")
