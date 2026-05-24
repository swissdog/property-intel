"""Quick integration test for the PRH business search connector.

Run with:
    python -m packages.connectors.prh.test_business_search
"""

from __future__ import annotations

import asyncio
import json
import sys

from .business_search import PRHBusinessSearch


async def main() -> None:
    prh = PRHBusinessSearch()
    try:
        # Test 1: Health check
        print("=" * 60)
        print("TEST 1: Health check")
        print("=" * 60)
        ok = await prh.health_check()
        print(f"  API reachable: {ok}")
        assert ok, "PRH API health check failed"
        print()

        # Test 2: Search by name
        print("=" * 60)
        print("TEST 2: Search parking companies by name")
        print("=" * 60)
        results = await prh.search(name="pysäköinti", location="Helsinki", max_results=10)
        print(f"  Found {len(results)} companies in Helsinki matching 'pysäköinti'")
        for r in results[:5]:
            print(f"    {r.business_id}: {r.name}")
            print(f"      Form: {r.company_form}")
            print(f"      BL: {r.business_line_code} - {r.business_line_description}")
            print(f"      Address: {r.street_address}, {r.post_code} {r.city}")
            print(f"      Registered: {r.registration_date}  Status: {r.status}")
            if r.auxiliary_names:
                print(f"      Also known as: {', '.join(r.auxiliary_names)}")
            print()

        # Test 3: Lookup by business ID
        print("=" * 60)
        print("TEST 3: Lookup Aimo Park by business ID")
        print("=" * 60)
        results = await prh.search(business_id="2208141-1")
        assert len(results) == 1, f"Expected 1 result, got {len(results)}"
        r = results[0]
        print(f"  {r.business_id}: {r.name}")
        print(f"  Form: {r.company_form}")
        print(f"  BL: {r.business_line_code} - {r.business_line_description}")
        print(f"  Address: {r.street_address}, {r.post_code} {r.city}")
        print(f"  Aux names: {r.auxiliary_names}")
        print()

        # Test 4: Search major parking operators
        print("=" * 60)
        print("TEST 4: Search major parking operators")
        print("=" * 60)
        operators = ["EuroPark", "Moovy", "Finnpark"]
        for name in operators:
            results = await prh.search(name=name, max_results=5)
            print(f"  '{name}': {len(results)} results")
            for r in results[:2]:
                print(f"    {r.business_id}: {r.name} ({r.business_line_description})")
            print()

        # Test 5: Dict output format
        print("=" * 60)
        print("TEST 5: Dict output format (JSON-serializable)")
        print("=" * 60)
        dict_data = await prh.search_dict(business_id="2208141-1")
        print(json.dumps(dict_data, indent=2, ensure_ascii=False))
        print()

        print("ALL TESTS PASSED")

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        await prh.close()


if __name__ == "__main__":
    asyncio.run(main())
