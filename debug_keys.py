from massive_tracker.config import CFG

print("--- KEY LENGTH CHECK ---")
key = CFG.massive_api_key
print(f"MASSIVE_API_KEY length: {len(key) if key else 0}")
print(f"MASSIVE_API_KEY (first 5): {key[:5] if key else 'None'}")
print("--- END ---")
