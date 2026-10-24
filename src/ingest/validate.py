"""Validation of store sales exports. Pure functions, no AWS calls, so they
can be unit tested without LocalStack."""
import csv
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

# Business dates are Jakarta days. WIB has no daylight saving, so a fixed
# offset is exact and avoids depending on tzdata inside the Lambda image.
JAKARTA = timezone(timedelta(hours=7))

REQUIRED = ["transaction_id", "store_id", "sold_at", "sku", "qty", "unit_price", "payment_method"]
PAYMENTS = {"cash", "card", "qris", "transfer"}
CURATED_COLUMNS = ["transaction_id", "store_id", "sold_at_utc", "business_date", "sku",
                   "qty", "unit_price", "amount", "payment_method"]
MAX_FUTURE = timedelta(minutes=10)  # tolerate till clocks that run slightly fast


class BadFile(Exception):
    """The file as a whole cannot be read; nothing from it is loaded."""


def read_rows(body: bytes) -> list[dict]:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise BadFile(f"not UTF-8 ({e.reason} at byte {e.start})") from None
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise BadFile("empty file")
    header = [h.strip().lower() for h in reader.fieldnames]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise BadFile(f"missing columns: {', '.join(missing)}")
    reader.fieldnames = header
    return list(reader)


def _money(value: str) -> Decimal:
    d = Decimal(value)
    if not d.is_finite() or d.as_tuple().exponent < -2:
        raise InvalidOperation
    return d


def check_row(row: dict, store_id: str, now: datetime) -> tuple[dict | None, str | None]:
    """Returns (curated row, None) or (None, reason)."""
    if None in row:
        return None, "too many fields"
    if any(row.get(c) is None for c in REQUIRED):
        return None, "too few fields"
    r = {k: (v or "").strip() for k, v in row.items()}

    if not r["transaction_id"]:
        return None, "transaction_id is empty"
    if r["store_id"] != store_id:
        return None, f"store_id {r['store_id']!r} does not match the file's store {store_id}"
    if not r["sku"]:
        return None, "sku is empty"
    try:
        sold_at = datetime.fromisoformat(r["sold_at"])
    except ValueError:
        return None, f"sold_at {r['sold_at']!r} is not an ISO timestamp"
    if sold_at.tzinfo is None:
        return None, "sold_at has no time zone offset"
    if sold_at > now + MAX_FUTURE:
        return None, "sold_at is in the future"
    try:
        qty = int(r["qty"])
    except ValueError:
        return None, f"qty {r['qty']!r} is not a whole number"
    if qty <= 0:
        return None, "qty must be positive"
    try:
        price = _money(r["unit_price"])
    except InvalidOperation:
        return None, f"unit_price {r['unit_price']!r} is not an amount"
    if price < 0:
        return None, "unit_price is negative"
    payment = r["payment_method"].lower()
    if payment not in PAYMENTS:
        return None, f"payment_method {r['payment_method']!r} is unknown"

    return {
        "transaction_id": r["transaction_id"],
        "store_id": store_id,
        "sold_at_utc": sold_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "business_date": sold_at.astimezone(JAKARTA).date().isoformat(),
        "sku": r["sku"],
        "qty": qty,
        "unit_price": price,
        "amount": price * qty,
        "payment_method": payment,
    }, None


def split(body: bytes, store_id: str, now: datetime) -> tuple[list[dict], list[dict]]:
    """Splits a file into curated rows and rejected rows (with line number and reason).
    A repeated transaction_id keeps its first occurrence."""
    good, bad, seen = [], [], set()
    for line, row in enumerate(read_rows(body), start=2):  # line 1 is the header
        clean, reason = check_row(row, store_id, now)
        if clean and clean["transaction_id"] in seen:
            clean, reason = None, "duplicate transaction_id in file"
        if clean:
            seen.add(clean["transaction_id"])
            good.append(clean)
        else:
            raw = {k: v for k, v in row.items() if k is not None}
            bad.append({"line": line, "reason": reason, **raw})
    return good, bad
