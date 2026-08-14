import argparse

from juyuan_update import config
from juyuan_update.db import connect, discover_benchmark, latest_curve_date, resolve_curve_codes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", action="store_true", help="also probe benchmark index tables")
    args = parser.parse_args()
    with connect() as conn:
        trade_date = latest_curve_date(conn)
        print(f"latest_curve_date={trade_date}")
        curves = resolve_curve_codes(conn, config.CURVE_DEFS, trade_date)
        print("resolved_curves:")
        for key, meta in curves.items():
            print(f"  {key}: {meta}")
        missing = [key for key in config.CURVE_DEFS if key not in curves]
        print(f"missing_curves={missing}")
        if args.benchmark:
            benchmark = discover_benchmark(conn, config.BENCHMARK_NAME_KEYWORD)
            print(f"benchmark={benchmark}")


if __name__ == "__main__":
    main()
