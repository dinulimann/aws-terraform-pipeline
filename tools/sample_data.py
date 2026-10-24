"""Synthetic store sales exports with known defects and the expected result.

    python tools/sample_data.py --out sample/   # writes files + truth.json

Each file is one store's export for one day. Some rows sold just before
midnight UTC fall on the next Jakarta day, so a file can feed two business
dates. Defects are injected at known positions so tests can check exactly
which rows end up in quarantine.
"""
import argparse
import csv
import io
import json
import random
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

JAKARTA = timezone(timedelta(hours=7))
HEADER = ["transaction_id", "store_id", "sold_at", "sku", "qty", "unit_price", "payment_method"]
SKUS = {f"SKU-{i:03d}": Decimal(random.Random(i).randrange(50, 2500)) * 100 for i in range(1, 41)}
PAYMENTS = ["cash", "card", "qris", "qris", "transfer"]

# defect name -> how it breaks a row (applied to a copy of a valid row)
DEFECTS = {
    "negative_qty": lambda r: {**r, "qty": "-2"},
    "bad_price": lambda r: {**r, "unit_price": "12.000,50"},
    "no_timezone": lambda r: {**r, "sold_at": r["sold_at"][:19]},
    "unknown_payment": lambda r: {**r, "payment_method": "voucher"},
    "wrong_store": lambda r: {**r, "store_id": "S99"},
    "empty_sku": lambda r: {**r, "sku": ""},
}


def make_file(store: str, day: date, rows: int, defects: list[str], seed: int,
              duplicate: bool = False) -> tuple[bytes, dict]:
    """Returns (csv bytes, expected) where expected has per-business-date
    totals of the valid rows and the number of quarantined rows."""
    rnd = random.Random(seed)
    out, valid = [], []
    opening = datetime.combine(day, time(8, 0), JAKARTA)
    for i in range(rows):
        sku = rnd.choice(list(SKUS))
        # trading 08:00-23:59 Jakarta, with a handful of late rows pushed past midnight
        sold = opening + timedelta(seconds=rnd.randrange(0, 16 * 3600))
        if i % 97 == 0:
            sold = datetime.combine(day, time(23, 50), JAKARTA) + timedelta(minutes=rnd.randrange(15, 60))
        # exports mix offsets: some tills write UTC, others local time
        stamp = sold.astimezone(timezone.utc) if i % 3 == 0 else sold
        row = {"transaction_id": f"{store}-{day:%Y%m%d}-{i:05d}", "store_id": store,
               "sold_at": stamp.isoformat(timespec="seconds"), "sku": sku,
               "qty": str(rnd.choice([1, 1, 1, 2, 2, 3, 6])), "unit_price": str(SKUS[sku]),
               "payment_method": rnd.choice(PAYMENTS)}
        out.append(row)
        valid.append((row, sold.date().isoformat()))

    bad_positions = rnd.sample(range(rows), len(defects))
    for pos, name in zip(bad_positions, defects):
        out[pos] = DEFECTS[name](out[pos])
    bad = set(bad_positions)
    if duplicate:  # the till re-sent its first valid row
        first = next(i for i in range(rows) if i not in bad)
        out.append(dict(out[first]))

    expected = {"rows_bad": len(defects) + int(duplicate), "dates": {}}
    for i, (row, business_date) in enumerate(valid):
        if i in bad:
            continue
        d = expected["dates"].setdefault(business_date, {"transactions": 0, "units": 0, "revenue": Decimal(0)})
        d["transactions"] += 1
        d["units"] += int(row["qty"])
        d["revenue"] += Decimal(row["unit_price"]) * int(row["qty"])
    expected["rows_ok"] = sum(d["transactions"] for d in expected["dates"].values())

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=HEADER, lineterminator="\n")
    w.writeheader()
    w.writerows(out)
    return buf.getvalue().encode(), expected


def sample_set(day: date) -> list[dict]:
    """A day's uploads: three clean stores, one with defects and a duplicate."""
    plan = [("S01", 1200, [], False), ("S02", 800, [], False), ("S03", 950, [], False),
            ("S04", 600, list(DEFECTS), True)]
    files = []
    for n, (store, rows, defects, dup) in enumerate(plan):
        body, expected = make_file(store, day, rows, defects, seed=day.toordinal() * 10 + n, duplicate=dup)
        files.append({"key": f"incoming/{store}/sales-{day}.csv", "body": body,
                      "store": store, "expected": expected})
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("sample"))
    ap.add_argument("--date", type=date.fromisoformat, default=date(2026, 10, 22))
    args = ap.parse_args()
    truth = {}
    for f in sample_set(args.date):
        path = args.out / f["key"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f["body"])
        truth[f["key"]] = f["expected"]
    (args.out / "truth.json").write_text(json.dumps(truth, indent=2, default=str))
    print(f"wrote {len(truth)} files to {args.out}/")


if __name__ == "__main__":
    main()
