"""
Test query normalization — verifies that semantically identical queries
produce the same normalized filter dict (and therefore the same cache key).

Run with:  python test_normalization.py
"""

from app.normalizer import normalize_query
from app.cache import make_cache_key


TEST_CASES = [
    # (label, query_a, query_b)  — must produce same normalized output
    (
        "Nigerian females 20-45",
        "Nigerian females between ages 20 and 45",
        "Women aged 20–45 living in Nigeria",
    ),
    (
        "Young males South Africa",
        "young males in South Africa",
        "Young men from South Africa",
    ),
    (
        "Elderly in Ghana",
        "elderly people in Ghana",
        "seniors from Ghana over 60",
    ),
    (
        "Gender only",
        "female",
        "women",
    ),
    (
        "Country demonym",
        "Kenyan profiles",
        "people from Kenya",
    ),
]

INEQUALITY_CASES = [
    # These should NOT produce the same output
    (
        "Different age ranges",
        "males aged 20-30",
        "males aged 30-40",
    ),
    (
        "Different countries",
        "females in Nigeria",
        "females in Ghana",
    ),
]


def run_tests():
    passed = 0
    failed = 0

    print("=" * 60)
    print("NORMALIZATION EQUALITY TESTS")
    print("=" * 60)
    for label, qa, qb in TEST_CASES:
        fa = normalize_query(qa)
        fb = normalize_query(qb)
        ka = make_cache_key("query", fa)
        kb = make_cache_key("query", fb)
        ok = ka == kb
        status = "✓ PASS" if ok else "✗ FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"\n{status}  [{label}]")
        print(f"  A: '{qa}' → {fa}")
        print(f"  B: '{qb}' → {fb}")
        if not ok:
            print(f"  KEY A: {ka}")
            print(f"  KEY B: {kb}")

    print("\n" + "=" * 60)
    print("NORMALIZATION INEQUALITY TESTS (should differ)")
    print("=" * 60)
    for label, qa, qb in INEQUALITY_CASES:
        fa = normalize_query(qa)
        fb = normalize_query(qb)
        ka = make_cache_key("query", fa)
        kb = make_cache_key("query", fb)
        ok = ka != kb
        status = "✓ PASS" if ok else "✗ FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"\n{status}  [{label}]")
        print(f"  A: '{qa}' → {fa}")
        print(f"  B: '{qb}' → {fb}")

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    import sys
    success = run_tests()
    sys.exit(0 if success else 1)