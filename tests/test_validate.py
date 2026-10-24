from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from sample_data import DEFECTS, make_file
from validate import BadFile, check_row, read_rows, split

NOW = datetime(2026, 10, 24, 3, 0, tzinfo=timezone.utc)
ROW = {"transaction_id": "T1", "store_id": "S01", "sold_at": "2026-10-22T23:30:00+07:00",
       "sku": "SKU-001", "qty": "2", "unit_price": "15000.50", "payment_method": "QRIS"}


def test_valid_row_is_normalised():
    row, reason = check_row(ROW, "S01", NOW)
    assert reason is None
    assert row["sold_at_utc"] == "2026-10-22T16:30:00+00:00"
    assert row["business_date"] == "2026-10-22"
    assert row["amount"] == Decimal("30001.00")
    assert row["payment_method"] == "qris"


def test_business_date_is_the_jakarta_day():
    # 17:30 UTC is 00:30 the next day in Jakarta
    row, _ = check_row({**ROW, "sold_at": "2026-10-22T17:30:00+00:00"}, "S01", NOW)
    assert row["business_date"] == "2026-10-23"


@pytest.mark.parametrize("change, reason", [
    ({"qty": "0"}, "qty must be positive"),
    ({"qty": "1.5"}, "not a whole number"),
    ({"unit_price": "1.005"}, "not an amount"),
    ({"unit_price": "NaN"}, "not an amount"),
    ({"unit_price": "-1"}, "negative"),
    ({"sold_at": "2026-10-22 23:30"}, "no time zone"),
    ({"sold_at": "22/10/2026"}, "not an ISO timestamp"),
    ({"sold_at": "2026-10-25T00:00:00+07:00"}, "in the future"),
    ({"payment_method": "voucher"}, "unknown"),
    ({"store_id": "S02"}, "does not match"),
    ({"transaction_id": " "}, "transaction_id is empty"),
])
def test_rejections(change, reason):
    row, why = check_row({**ROW, **change}, "S01", NOW)
    assert row is None and reason in why


def test_header_is_case_and_space_insensitive_and_bom_is_ignored():
    body = "﻿Transaction_ID, Store_ID ,sold_at,SKU,qty,unit_price,payment_method\nT1,S01,2026-10-22T10:00:00+07:00,A,1,10,cash\n"
    good, bad = split(body.encode(), "S01", NOW)
    assert len(good) == 1 and not bad


@pytest.mark.parametrize("body, reason", [
    (b"", "empty file"),
    (b"id,store\n1,S01\n", "missing columns"),
    ("transaction_id;store_id\n".encode("utf-16"), "not UTF-8"),
])
def test_unreadable_files(body, reason):
    with pytest.raises(BadFile, match=reason):
        read_rows(body)


def test_ragged_rows_are_quarantined():
    header = ",".join(ROW)
    body = f"{header}\nT1,S01,2026-10-22T10:00:00+07:00,A,1,10,cash,extra\nT2,S01\n".encode()
    good, bad = split(body, "S01", NOW)
    assert not good
    assert [b["reason"] for b in bad] == ["too many fields", "too few fields"]
    assert [b["line"] for b in bad] == [2, 3]


def test_generated_file_matches_its_expected_result():
    body, expected = make_file("S04", date(2026, 10, 22), 500, list(DEFECTS), seed=7, duplicate=True)
    good, bad = split(body, "S04", NOW)
    assert len(bad) == expected["rows_bad"] == len(DEFECTS) + 1
    assert "duplicate transaction_id in file" in {b["reason"] for b in bad}
    by_date = {}
    for r in good:
        by_date.setdefault(r["business_date"], Decimal(0))
        by_date[r["business_date"]] += r["amount"]
    assert by_date == {d: v["revenue"] for d, v in expected["dates"].items()}
    assert len(by_date) == 2  # late sales spill into the next Jakarta day
