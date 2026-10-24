"""Daily report, triggered by EventBridge shortly after midnight Jakarta time.

Reports the previous Jakarta day: sales per store (from the SALES# counters)
and ingest health for files processed that day (from the INGEST# log).
Writes reports/daily/date=YYYY-MM-DD/report.json to the lake bucket.
An event {"date": "YYYY-MM-DD"} reruns a specific day.
"""
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

LAKE = os.environ["LAKE_BUCKET"]
TABLE = os.environ["TABLE_NAME"]
JAKARTA = timezone(timedelta(hours=7))

ddb = boto3.client("dynamodb")
s3 = boto3.client("s3")


def handler(event, context):
    day = report_day(event)
    sales = query(f"SALES#{day}")
    files = query(f"INGEST#{day}")

    stores = sorted(({
        "store_id": i["sk"]["S"].removeprefix("STORE#"),
        "transactions": int(i["transactions"]["N"]),
        "units": int(i["units"]["N"]),
        "revenue": str(Decimal(i["revenue"]["N"])),
    } for i in sales), key=lambda s: s["store_id"])
    statuses = {}
    for f in files:
        statuses[f["status"]["S"]] = statuses.get(f["status"]["S"], 0) + 1
    report = {
        "business_date": day,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sales": {
            "stores": stores,
            "transactions": sum(s["transactions"] for s in stores),
            "revenue": str(sum((Decimal(s["revenue"]) for s in stores), Decimal(0))),
        },
        "ingest": {
            "files": len(files),
            "by_status": dict(sorted(statuses.items())),
            "rows_ok": sum(int(f["rows_ok"]["N"]) for f in files),
            "rows_quarantined": sum(int(f["rows_bad"]["N"]) for f in files),
            "rejected_files": [{"source": f["sk"]["S"].removeprefix("FILE#").rsplit("#", 1)[0],
                                "reason": f.get("reason", {}).get("S")}
                               for f in files if f["status"]["S"] == "rejected"],
        },
    }
    key = f"reports/daily/date={day}/report.json"
    s3.put_object(Bucket=LAKE, Key=key, Body=json.dumps(report, indent=2).encode(),
                  ContentType="application/json")
    log.info(json.dumps({"report": key, "transactions": report["sales"]["transactions"],
                         "files": len(files)}))
    return {"key": key, **report}


def report_day(event: dict) -> str:
    if event.get("date"):
        return date.fromisoformat(event["date"]).isoformat()
    # Scheduled events carry the trigger time in "time" (UTC).
    fired = datetime.fromisoformat(event["time"].replace("Z", "+00:00")) if event.get("time") \
        else datetime.now(timezone.utc)
    return (fired.astimezone(JAKARTA).date() - timedelta(days=1)).isoformat()


def query(pk: str) -> list[dict]:
    items, kwargs = [], {}
    while True:
        page = ddb.query(TableName=TABLE, KeyConditionExpression="pk = :pk",
                         ExpressionAttributeValues={":pk": {"S": pk}}, **kwargs)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
