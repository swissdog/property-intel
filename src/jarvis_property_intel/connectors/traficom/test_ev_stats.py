"""Quick integration test for the Traficom EV stats connector.

Run with:
    python -m packages.connectors.traficom.test_ev_stats
"""

from __future__ import annotations

import asyncio
import sys

from .ev_stats import TraficomEVStats


async def main() -> None:
    ev = TraficomEVStats()
    try:
        # Test 1: Fetch municipality codes
        print("=" * 60)
        print("TEST 1: Fetch municipality codes from table metadata")
        print("=" * 60)
        codes = await ev.fetch_municipality_codes()
        print(f"  Found {len(codes)} municipalities")
        # Show a few examples
        sample = list(codes.items())[:5]
        for code, name in sample:
            print(f"    {code} = {name}")
        print()

        # Test 2: Fetch EV stats for a few cities
        test_cities = ["KU091", "KU049", "KU853", "KU837", "KU564"]
        city_names = [codes.get(c, c) for c in test_cities]
        print("=" * 60)
        print(f"TEST 2: Fetch EV stats for {', '.join(city_names)}")
        print("=" * 60)
        data = await ev.fetch_ev_stats(municipality_codes=test_cities)

        for name, d in sorted(data.items(), key=lambda x: -x[1].ev_pct):
            print(f"  {name}:")
            print(f"    Total vehicles: {d.total_vehicles:,}")
            print(f"    BEV:            {d.bev_count:,}")
            print(f"    PHEV:           {d.phev_count:,}")
            print(f"    EV total:       {d.ev_total:,}")
            print(f"    EV share:       {d.ev_pct:.1f}%")
            print()

        # Test 3: dict output format
        print("=" * 60)
        print("TEST 3: Dict output format (JSON-serializable)")
        print("=" * 60)
        dict_data = await ev.fetch_ev_stats_dict(municipality_codes=test_cities[:2])
        import json
        print(json.dumps(dict_data, indent=2, ensure_ascii=False))
        print()

        print("ALL TESTS PASSED")

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        await ev.close()


if __name__ == "__main__":
    asyncio.run(main())
