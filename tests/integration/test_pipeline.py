"""End-to-end tests against the stack applied to LocalStack (envs/local).

Each session uses a random past business date, so the tests can be rerun on
the same stack without tearing it down.
"""
import csv
import gzip
import io
import json
import os
import random
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import boto3
import pytest

from sample_data import sample_set

ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
JAKARTA = timezone(timedelta(hours=7))
DAY = date(2020, 1, 1) + timedelta(days=random.randrange(2000))


def client(name):
    return boto3.client(name, endpoint_url=ENDPOINT, region_name="ap-southeast-3",
                        aws_access_key_id="test", aws_secret_access_key="test")


s3, ddb, lam, sqs, events = (client(n) for n in ("s3", "dynamodb", "lambda", "sqs", "events"))


@pytest.fixture(scope="session")
def out():
    tf = os.environ.get("TERRAFORM", "terraform")
    raw = subprocess.check_output([tf, f"-chdir={ROOT / 'envs/local'}", "output", "-json"])
    return json.loads(raw)["pipeline"]["value"]


def upload(out, key, body) -> str:
    return s3.put_object(Bucket=out["landing_bucket"], Key=key, Body=body)["ETag"].strip('"')


def wait_processed(out, key, etag, timeout=120) -> dict:
    """Waits for the idempotency marker, then returns the ingest log entry."""
    source = f"s3://{out['landing_bucket']}/{key}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        m = ddb.get_item(TableName=out["table_name"],
                         Key={"pk": {"S": f"FILE#{source}"}, "sk": {"S": f"ETAG#{etag}"}}).get("Item")
        if m:
            e = ddb.get_item(TableName=out["table_name"], Key={
                "pk": {"S": f"INGEST#{m['processed_on']['S']}"},
                "sk": {"S": f"FILE#{source}#{etag}"}})["Item"]
            return {k: next(iter(v.values())) for k, v in e.items()}
        time.sleep(1)
    raise AssertionError(f"{key} was not processed within {timeout}s")


def sales(out, day: str) -> dict:
    items = ddb.query(TableName=out["table_name"], KeyConditionExpression="pk = :p",
                      ExpressionAttributeValues={":p": {"S": f"SALES#{day}"}})["Items"]
    return {i["sk"]["S"][6:]: {"transactions": int(i["transactions"]["N"]), "units": int(i["units"]["N"]),
                               "revenue": Decimal(i["revenue"]["N"])} for i in items}


@pytest.fixture(scope="session")
def loaded(out):
    files = sample_set(DAY)
    for f in files:
        f["etag"] = upload(out, f["key"], f["body"])
    for f in files:
        f["entry"] = wait_processed(out, f["key"], f["etag"])
    return files


def expected_sales(files) -> dict:
    exp = {}
    for f in files:
        for day, v in f["expected"]["dates"].items():
            exp.setdefault(day, {})[f["store"]] = v
    return exp


def test_rows_are_counted_exactly(out, loaded):
    for f in loaded:
        e, x = f["entry"], f["expected"]
        assert (int(e["rows_ok"]), int(e["rows_bad"])) == (x["rows_ok"], x["rows_bad"]), f["key"]
        assert e["status"] == ("partial" if x["rows_bad"] else "loaded")
    exp = expected_sales(loaded)
    assert len(exp) == 2  # late sales land on the next Jakarta day
    for day, stores in exp.items():
        assert sales(out, day) == stores


def test_curated_files_are_partitioned_and_complete(out, loaded):
    for f in loaded:
        for day, x in f["expected"]["dates"].items():
            prefix = f"curated/sales/business_date={day}/store_id={f['store']}/"
            keys = [o["Key"] for o in s3.list_objects_v2(Bucket=out["lake_bucket"], Prefix=prefix)["Contents"]]
            assert len(keys) == 1 and keys[0].endswith(f"-{f['etag'][:8]}.csv.gz")
            body = s3.get_object(Bucket=out["lake_bucket"], Key=keys[0])["Body"].read()
            rows = list(csv.DictReader(io.StringIO(gzip.decompress(body).decode())))
            assert len(rows) == x["transactions"]
            assert sum(Decimal(r["amount"]) for r in rows) == x["revenue"]
            assert {r["business_date"] for r in rows} == {day}


def test_bad_rows_are_quarantined_with_reasons(out, loaded):
    f = next(f for f in loaded if f["expected"]["rows_bad"])
    key = f"quarantine/rows/store_id={f['store']}/sales-{DAY}-{f['etag'][:8]}.csv"
    rows = list(csv.DictReader(io.StringIO(
        s3.get_object(Bucket=out["lake_bucket"], Key=key)["Body"].read().decode())))
    assert len(rows) == f["expected"]["rows_bad"]
    reasons = " | ".join(r["reason"] for r in rows)
    for fragment in ["qty must be positive", "unit_price", "no time zone", "payment_method",
                     "does not match", "sku is empty", "duplicate transaction_id"]:
        assert fragment in reasons
    assert all(r["line"].isdigit() and r["transaction_id"] for r in rows)


def test_reupload_of_the_same_file_is_counted_once(out, loaded):
    f = loaded[0]
    before = {d: sales(out, d) for d in f["expected"]["dates"]}
    assert upload(out, f["key"], f["body"]) == f["etag"]  # same bytes, same ETag
    # Invoke directly as well, so the test sees the outcome of a duplicate event.
    event = {"Records": [{"s3": {"bucket": {"name": out["landing_bucket"]}, "object": {"key": f["key"]}}}]}
    res = json.loads(lam.invoke(FunctionName=out["ingest_function"], Payload=json.dumps(event))["Payload"].read())
    assert res["results"][0]["status"] == "duplicate"
    time.sleep(5)  # let the S3-triggered invocation finish too
    assert {d: sales(out, d) for d in f["expected"]["dates"]} == before


def test_unreadable_file_is_rejected_whole(out):
    key = f"incoming/S05/export-{DAY}.csv"
    etag = upload(out, key, b"id;store;total\n1;S05;10000\n")
    e = wait_processed(out, key, etag)
    assert e["status"] == "rejected" and "missing columns" in e["reason"]
    head = s3.head_object(Bucket=out["lake_bucket"], Key=f"quarantine/files/store_id=S05/export-{DAY}-{etag[:8]}.csv")
    assert "missing columns" in head["Metadata"]["reason"]
    assert sales(out, str(DAY)).get("S05") is None


def test_processing_failure_lands_in_the_dead_letter_queue(out):
    missing = f"incoming/S06/deleted-before-processing-{DAY}-{random.randrange(10**9)}.csv"
    event = {"Records": [{"s3": {"bucket": {"name": out["landing_bucket"]}, "object": {"key": missing}}}]}
    lam.invoke(FunctionName=out["ingest_function"], InvocationType="Event", Payload=json.dumps(event))
    deadline = time.time() + 120
    while time.time() < deadline:
        for m in sqs.receive_message(QueueUrl=out["dlq_url"], MaxNumberOfMessages=10,
                                     WaitTimeSeconds=5).get("Messages", []):
            if missing in m["Body"]:
                sqs.delete_message(QueueUrl=out["dlq_url"], ReceiptHandle=m["ReceiptHandle"])
                return
    raise AssertionError("failed event did not reach the DLQ")


def test_daily_report_matches_the_counters(out, loaded):
    # Shaped like the scheduled event: fires 00:10 Jakarta, i.e. 17:10 UTC the day before.
    fired = {"source": "aws.events", "detail-type": "Scheduled Event", "time": f"{DAY}T17:10:00Z"}
    res = json.loads(lam.invoke(FunctionName=out["report_function"], Payload=json.dumps(fired))["Payload"].read())
    assert res["business_date"] == str(DAY)
    report = json.loads(s3.get_object(Bucket=out["lake_bucket"], Key=res["key"])["Body"].read())
    exp = expected_sales(loaded)[str(DAY)]
    assert {s["store_id"]: Decimal(s["revenue"]) for s in report["sales"]["stores"]} == \
        {k: v["revenue"] for k, v in exp.items()}
    assert report["sales"]["transactions"] == sum(v["transactions"] for v in exp.values())


def test_report_includes_ingest_health_for_today(out, loaded):
    today = datetime.now(JAKARTA).date().isoformat()
    res = json.loads(lam.invoke(FunctionName=out["report_function"],
                                Payload=json.dumps({"date": today}))["Payload"].read())
    ingest = res["ingest"]
    assert ingest["by_status"].get("partial", 0) >= 1
    assert ingest["rows_quarantined"] >= sum(f["expected"]["rows_bad"] for f in loaded)


def test_report_is_scheduled_after_midnight_jakarta(out):
    rule = events.describe_rule(Name=out["report_rule"])
    assert rule["ScheduleExpression"] == "cron(10 17 * * ? *)"
    targets = events.list_targets_by_rule(Rule=out["report_rule"])["Targets"]
    assert [t["Arn"].rsplit(":", 1)[-1] for t in targets] == [out["report_function"]]


def test_iam_policies_are_least_privilege(out):
    def grants(policy):
        g = {}
        for st in policy["Statement"]:
            assert st["Effect"] == "Allow"
            actions = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
            resources = st["Resource"] if isinstance(st["Resource"], list) else [st["Resource"]]
            for a in actions:
                assert "*" not in a, a
                for r in resources:
                    assert r != "*" and not r.endswith(":*:*"), r
                    g.setdefault(a, set()).add(r.split(":::")[-1] if r.startswith("arn:aws:s3") else r)
        return g

    ingest, report = grants(out["iam_policies"]["ingest"]), grants(out["iam_policies"]["report"])
    lake, landing = out["lake_bucket"], out["landing_bucket"]
    assert ingest["s3:GetObject"] == {f"{landing}/incoming/*"}
    assert ingest["s3:PutObject"] == {f"{lake}/curated/*", f"{lake}/quarantine/*"}
    assert report["s3:PutObject"] == {f"{lake}/reports/*"}
    assert "s3:GetObject" not in report and "dynamodb:PutItem" not in report
    assert not any(a.startswith(("s3:Delete", "dynamodb:Delete", "dynamodb:Scan")) for a in {**ingest, **report})
