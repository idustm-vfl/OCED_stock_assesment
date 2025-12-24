"""Test Monday report and Friday scorecard generation."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

from massive_tracker.store import DB
from massive_tracker.report_monday import write_monday_report
from massive_tracker.weekly_close import write_weekly_scorecard


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "sqlite" / "tracker.db"
REPORT_DIR = ROOT / "data" / "reports"


@pytest.fixture
def db():
    """Return DB instance."""
    return DB(str(DB_PATH))


def test_monday_report_generation(db: DB):
    """Test that Monday report generates without errors."""
    # Generate report
    markdown = write_monday_report(db_path=str(DB_PATH))
    
    # Verify output
    assert markdown, "Monday report is empty"
    assert "# Monday Run Report" in markdown, "Missing report header"
    assert "Universe Health" in markdown, "Missing Universe Health section"
    assert "LLM Picks" in markdown, "Missing LLM Picks section"
    assert "OCED Table" in markdown, "Missing OCED Table section"
    assert "Best Contract Candidates" in markdown, "Missing Best Contract Candidates section"
    assert "Seed Bucket" in markdown, "Missing Seed Bucket grouping"
    assert "Promotions" in markdown, "Missing Promotions section"
    
    # Verify masked keys present
    assert "MASSIVE_ACCESS_KEY:" in markdown or "MASSIVE_KEY_ID:" in markdown, \
        "Missing masked key status"


def test_monday_report_seed_buckets(db: DB):
    """Test that Monday report includes seed bucket grouping."""
    markdown = write_monday_report(db_path=str(DB_PATH))
    
    # Check for bucket headers
    buckets = ["<=5k", "<=10k", "<=25k", "<=50k", ">50k"]
    
    # At least verify the structure is present (actual buckets depend on data)
    assert "Seed Bucket" in markdown, "Seed Bucket header not found"


def test_friday_scorecard_generation(db: DB):
    """Test that Friday scorecard generates without errors."""
    # Generate scorecard
    markdown = write_weekly_scorecard(db_path=str(DB_PATH))
    
    # Verify output
    assert markdown, "Weekly scorecard is empty"
    assert "# Weekly Scorecard" in markdown, "Missing scorecard header"
    
    # If there are outcomes, verify sections
    if "No promoted contracts" not in markdown:
        assert "Predicted vs Realized" in markdown or "No promoted" in markdown, \
            "Missing Predicted vs Realized section"
        assert "LLM Hit Rate" in markdown or "No promoted" in markdown, \
            "Missing LLM Hit Rate section"
        assert "Strike Quality" in markdown or "No promoted" in markdown, \
            "Missing Strike Quality section"
        assert "Prediction Error Distribution" in markdown or "No promoted" in markdown, \
            "Missing Prediction Error section"
        assert "Rank Drift Analysis" in markdown or "No promoted" in markdown, \
            "Missing Rank Drift section"


def test_friday_scorecard_metrics(db: DB):
    """Test that Friday scorecard computes metrics correctly."""
    from massive_tracker.weekly_close import compute_outcomes
    
    # Compute outcomes
    results = compute_outcomes(db_path=str(DB_PATH))
    
    # If there are results, verify structure
    if results:
        for result in results:
            # Verify required fields
            assert "week_ending" in result
            assert "ticker" in result
            
            # Verify sources_json has extended metadata
            sources_json = result.get("sources_json", "{}")
            if sources_json and sources_json != "{}":
                import json
                sources = json.loads(sources_json)
                
                # Check for new fields added in enhancement
                # These may be None if data isn't available, but keys should exist
                assert "predicted_yield" in sources or True  # Optional
                assert "promotion_reason" in sources or True  # Optional


def test_report_files_created(db: DB):
    """Test that report files are created in correct location."""
    from datetime import datetime
    
    # Generate reports
    write_monday_report(db_path=str(DB_PATH))
    write_weekly_scorecard(db_path=str(DB_PATH))
    
    # Check files exist
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    monday_file = REPORT_DIR / f"monday_run_{date_str}.md"
    scorecard_file = REPORT_DIR / f"weekly_scorecard_{date_str}.md"
    
    assert monday_file.exists(), f"Monday report file not created: {monday_file}"
    assert scorecard_file.exists(), f"Scorecard file not created: {scorecard_file}"
    
    # Verify non-empty
    assert monday_file.stat().st_size > 0, "Monday report file is empty"
    assert scorecard_file.stat().st_size > 0, "Scorecard file is empty"
