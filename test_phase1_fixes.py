#!/usr/bin/env python3
"""
Test suite for Phase 1: Fix Critical Connection Issues

This test validates all the fixes implemented in Phase 1:
1. Time module import in massive_client.py
2. S3 credential mapping in config.py
3. DB singleton pattern in store.py
4. All modules updated to use get_db()
5. Duplicate S3 client removed
6. Config cache duplication removed
"""

import sys
import os


def test_time_import():
    """Test 1: Verify time module is imported in massive_client.py"""
    print("\n[TEST 1] Time module import")
    with open('massive_tracker/massive_client.py') as f:
        content = f.read()
        assert 'import time' in content, "time module not imported"
        # Verify it's used in _throttle function
        assert 'time.time()' in content, "time.time() not used"
        assert 'time.sleep(' in content, "time.sleep() not used"
    print("✅ PASS: time module properly imported and used")


def test_s3_credentials():
    """Test 2: Verify S3 credential mapping"""
    print("\n[TEST 2] S3 credential mapping")
    with open('massive_tracker/config.py') as f:
        content = f.read()
        
        # Find the load_flatfile_config function
        func_start = content.find('def load_flatfile_config')
        func_end = content.find('\ndef ', func_start + 1)
        if func_end == -1:
            func_end = len(content)
        func_content = content[func_start:func_end]
        
        # Verify correct env vars are used
        assert 'MASSIVE_KEY_ID' in func_content, "MASSIVE_KEY_ID not found"
        assert 'AWS_ACCESS_KEY_ID' in func_content, "AWS_ACCESS_KEY_ID not found"
        assert 'MASSIVE_SECRET_KEY' in func_content, "MASSIVE_SECRET_KEY not found"
        assert 'AWS_SECRET_ACCESS_KEY' in func_content, "AWS_SECRET_ACCESS_KEY not found"
        
        # Verify incorrect vars are NOT used in load_flatfile_config
        # Note: MASSIVE_API_KEY may still exist elsewhere in config.py
        # for runtime config, but should not be used for S3 flatfile access
        lines = func_content.split('\n')
        for line in lines:
            if 'access_key = _first_env' in line:
            assert 'MASSIVE_API_KEY' not in line, "Still using MASSIVE_API_KEY for access_key"
            if 'secret_key = _first_env' in line:
                assert 'MASSIVE_API_KEY' not in line or 'MASSIVE_SECRET_KEY' in line, "Still using MASSIVE_API_KEY for secret_key"
    
    print("✅ PASS: S3 credentials use correct environment variables")


def test_db_singleton():
    """Test 3: Verify DB singleton pattern"""
    print("\n[TEST 3] DB singleton pattern")
    
    # Import get_db
    from massive_tracker.store import get_db
    
    # Test 3a: Same path returns same instance
    db1 = get_db('test_path.db')
    db2 = get_db('test_path.db')
    assert db1 is db2, "get_db() should return same instance for same path"
    print("  ✓ Same path returns singleton instance")
    
    # Test 3b: Different path creates new instance
    db3 = get_db('different_path.db')
    assert db3 is not db1, "get_db() should create new instance for different path"
    print("  ✓ Different path creates new instance")
    
    # Test 3c: Verify path is tracked
    assert db1.path == 'test_path.db', "DB instance should track its path"
    assert db3.path == 'different_path.db', "DB instance should track its path"
    print("  ✓ DB instances track their paths")
    
    print("✅ PASS: DB singleton pattern working correctly")


def test_modules_use_get_db():
    """Test 4: Verify all specified modules use get_db()"""
    print("\n[TEST 4] Modules using get_db()")
    
    files_to_check = [
        'massive_tracker/cli.py',
        'massive_tracker/promotion.py',
        'massive_tracker/batch.py',
        'massive_tracker/scorecard_app.py',
        'massive_tracker/flatfile_manager.py',
        'massive_tracker/compare_models.py',
        'massive_tracker/covered_calls.py',
        'massive_tracker/monitor.py',
        'massive_tracker/oced.py',
        'massive_tracker/flatfiles.py',
        'diag.py',
        'debug_picker.py',
        'diagnose_data.py',
        'validate_picker.py',
    ]
    
    for filepath in files_to_check:
        with open(filepath) as f:
            content = f.read()
            
            # Check if file uses database
            if 'DB(' in content or 'get_db(' in content:
                # If it instantiates DB, should use get_db
                import re
                db_instantiations = re.findall(r'\bDB\([^)]*\)', content)
                
                # Filter out type hints and class definitions
                actual_instantiations = [
                    inst for inst in db_instantiations
                    if 'db: DB' not in inst and 'DB(' in inst
                ]
                
                if actual_instantiations:
                    # Check if they're all in comments or type hints
                    for inst in actual_instantiations:
                        # Look for the line containing this instantiation
                        for line in content.split('\n'):
                            if inst in line and not line.strip().startswith('#'):
                                # This is a real instantiation
                                assert False, f"{filepath} still has DB instantiation: {inst}"
                
                # Should import get_db if using database
                if 'get_db(' in content:
                    assert ('from .store import get_db' in content or 
                           'from massive_tracker.store import get_db' in content), \
                           f"{filepath} uses get_db but doesn't import it"
    
    print("✅ PASS: All specified modules use get_db()")


def test_duplicate_code_removed():
    """Test 5: Verify duplicate code removed"""
    print("\n[TEST 5] Duplicate code removal")
    
    # Check flatfiles.py
    with open('massive_tracker/flatfiles.py') as f:
        content = f.read()
        
        assert 'def s3_client_from_cfg' not in content, \
            "s3_client_from_cfg function should be removed"
        print("  ✓ s3_client_from_cfg removed from flatfiles.py")
        
        assert 'def _get_cfg' not in content, \
            "_get_cfg function should be removed"
        print("  ✓ _get_cfg removed from flatfiles.py")
        
        assert '_flat_cfg_cache' not in content, \
            "_flat_cfg_cache global should be removed"
        print("  ✓ _flat_cfg_cache removed from flatfiles.py")
        
        # Verify MassiveS3 is used instead
        assert 'from .s3_flatfiles import MassiveS3' in content, \
            "Should import MassiveS3"
        assert 'MassiveS3(cfg)' in content, \
            "Should use MassiveS3 class"
        print("  ✓ MassiveS3 class used")
    
    # Check cli.py
    with open('massive_tracker/cli.py') as f:
        content = f.read()
        
        assert 'def _cfg()' not in content, \
            "_cfg() function should be removed"
        print("  ✓ _cfg() removed from cli.py")
        
        assert '_CFG_CACHE = None' not in content, \
            "_CFG_CACHE global should be removed"
        print("  ✓ _CFG_CACHE removed from cli.py")
    
    print("✅ PASS: All duplicate code removed")


def test_load_flatfile_config_usage():
    """Test 6: Verify load_flatfile_config is used directly"""
    print("\n[TEST 6] Direct load_flatfile_config usage")
    
    with open('massive_tracker/flatfiles.py') as f:
        content = f.read()
        
        # Should use load_flatfile_config directly
        assert 'load_flatfile_config(' in content, \
            "Should use load_flatfile_config()"
        
        # Should not have cache wrapper
        assert 'def _get_cfg' not in content, \
            "Should not have _get_cfg wrapper"
        
        print("  ✓ Using load_flatfile_config() directly")
    
    print("✅ PASS: Config loading is direct, no caching wrapper")


def main():
    """Run all tests"""
    print("=" * 70)
    print("PHASE 1: CRITICAL CONNECTION ISSUES - TEST SUITE")
    print("=" * 70)
    
    tests = [
        ("Time module import", test_time_import),
        ("S3 credential mapping", test_s3_credentials),
        ("DB singleton pattern", test_db_singleton),
        ("Modules use get_db()", test_modules_use_get_db),
        ("Duplicate code removed", test_duplicate_code_removed),
        ("Direct config usage", test_load_flatfile_config_usage),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"❌ FAIL: {name} - {e}")
            failed += 1
        except Exception as e:
            print(f"❌ ERROR: {name} - {e}")
            failed += 1
    
    print("\n" + "=" * 70)
    print(f"TEST RESULTS: {passed} passed, {failed} failed")
    print("=" * 70)
    
    if failed > 0:
        print("\n❌ Some tests failed")
        sys.exit(1)
    else:
        print("\n✅ ALL TESTS PASSED")
        print("\nPhase 1 fixes verified:")
        print("  1. ✅ Time module properly imported")
        print("  2. ✅ S3 credentials use correct env vars")
        print("  3. ✅ DB singleton pattern implemented")
        print("  4. ✅ All modules use get_db()")
        print("  5. ✅ Duplicate code removed")
        print("  6. ✅ Config loading simplified")
        sys.exit(0)


if __name__ == '__main__':
    main()
