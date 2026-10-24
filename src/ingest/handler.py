"""S3 ObjectCreated -> validate -> curated / quarantine in the lake bucket,
counters in DynamoDB.

Idempotent per object version: the DynamoDB transaction that adds the
counters also writes a marker for (key, ETag) with a condition, so an S3
retry, a Lambda retry or a re-upload of identical bytes is counted once.
Curated and quarantine keys are deterministic, so rewriting them is harmless.

Data problems (bad rows, unreadable files) are quarantined and the function
succeeds. Anything else raises, Lambda retries, and the event ends up in the
SQS dead-letter queue.
"""
import csv
import gzip
import io
import json
import logging
import os
import re
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

from validate import CURATED_COLUMNS, JAKARTA, REQUIRED, BadFile, split

log = logging.getLogger()
log.setLevel(logging.INFO)

LAKE = os.environ["LAKE_BUCKET"]
TABLE = os.environ["TABLE_NAME"]
MAX_BYTES = int(os.environ.get("MAX_BYTES", 50 * 1024 * 1024))
KEY = re.compile(r"^incoming/(?P<store>[A-Z0-9][A-Z0-9-]{0,15})/(?P<name>[^/]+)\.csv$")
MAX_DATES = 90  # one DynamoDB transaction holds at most 100 items

s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")


def handler(event, context):
    results = []
    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        results.append(process(bucket, key))
    return {"results": results}


def process(bucket: str, key: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    obj = s3.get_object(Bucket=bucket, Key=key)  # NoSuchKey etc. -> retry -> DLQ
    etag = obj["ETag"].strip('"')
    m = KEY.match(key)
    store = m["store"] if m else "unknown"
    stem = f"{m['name'] if m else key.rsplit('/', 1)[-1]}-{etag[:8]}"
    base = {"source": f"s3://{bucket}/{key}", "etag": etag, "store_id": store}

    try:
        if not m:
            raise BadFile("key is not incoming/<STORE>/<name>.csv")
        if obj["ContentLength"] > MAX_BYTES:
            raise BadFile(f"file is larger than {MAX_BYTES} bytes")
        body = obj["Body"].read()
        good, bad = split(body, store, now)
        by_date = defaultdict(list)
        for row in good:
            by_date[row["business_date"]].append(row)
        if len(by_date) > MAX_DATES:
            raise BadFile(f"rows span {len(by_date)} business dates (limit {MAX_DATES})")
    except BadFile as e:
        if m and obj["ContentLength"] <= MAX_BYTES:
            s3.put_object(Bucket=LAKE, Key=f"quarantine/files/store_id={store}/{stem}.csv",
                          Body=body, Metadata={"reason": str(e)[:1000]})
        outcome = {**base, "status": "rejected", "reason": str(e), "rows_ok": 0, "rows_bad": 0}
        return record(outcome, {}, now)

    for date, rows in by_date.items():
        s3.put_object(Bucket=LAKE,
                      Key=f"curated/sales/business_date={date}/store_id={store}/{stem}.csv.gz",
                      Body=gzip.compress(to_csv(rows, CURATED_COLUMNS).encode(), mtime=0),
                      ContentType="text/csv", ContentEncoding="gzip")
    if bad:
        s3.put_object(Bucket=LAKE, Key=f"quarantine/rows/store_id={store}/{stem}.csv",
                      Body=to_csv(bad, ["line", "reason", *REQUIRED]).encode(), ContentType="text/csv")

    outcome = {**base, "status": "loaded" if not bad else "partial",
               "rows_ok": len(good), "rows_bad": len(bad)}
    return record(outcome, by_date, now)


def record(outcome: dict, by_date: dict, now: datetime) -> dict:
    """One transaction: the (key, ETag) marker, the ingest log entry and the
    per-day sales counters. If the marker exists, nothing is added."""
    processed_on = now.astimezone(JAKARTA).date().isoformat()
    marker = {"pk": {"S": f"FILE#{outcome['source']}"}, "sk": {"S": f"ETAG#{outcome['etag']}"},
              "processed_on": {"S": processed_on}}
    entry = {"pk": {"S": f"INGEST#{processed_on}"},
             "sk": {"S": f"FILE#{outcome['source']}#{outcome['etag']}"},
             "status": {"S": outcome["status"]}, "store_id": {"S": outcome["store_id"]},
             "rows_ok": {"N": str(outcome["rows_ok"])}, "rows_bad": {"N": str(outcome["rows_bad"])},
             "processed_at": {"S": now.isoformat(timespec="seconds")}}
    if outcome.get("reason"):
        entry["reason"] = {"S": outcome["reason"]}

    items = [
        {"Put": {"TableName": TABLE, "Item": marker,
                 "ConditionExpression": "attribute_not_exists(pk)"}},
        {"Put": {"TableName": TABLE, "Item": entry}},
    ]
    for date, rows in sorted(by_date.items()):
        items.append({"Update": {
            "TableName": TABLE,
            "Key": {"pk": {"S": f"SALES#{date}"}, "sk": {"S": f"STORE#{outcome['store_id']}"}},
            "UpdateExpression": "ADD transactions :n, units :u, revenue :r, files :one",
            "ExpressionAttributeValues": {
                ":n": {"N": str(len(rows))},
                ":u": {"N": str(sum(r["qty"] for r in rows))},
                ":r": {"N": str(sum(r["amount"] for r in rows))},
                ":one": {"N": "1"},
            }}})

    try:
        ddb.transact_write_items(TransactItems=items)
    except ClientError as e:
        reasons = e.response.get("CancellationReasons") or []
        if reasons and reasons[0].get("Code") == "ConditionalCheckFailed":
            outcome = {**outcome, "status": "duplicate"}
            log.info(json.dumps(outcome))
            return outcome
        raise
    log.info(json.dumps(outcome))
    return outcome


def to_csv(rows: list[dict], columns: list[str]) -> str:
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return out.getvalue()
