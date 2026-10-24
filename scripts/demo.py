"""Uploads one day of store exports to the LocalStack stack, waits for the
pipeline, then runs the daily report and prints what landed where."""
import json
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests" / "integration"))
from sample_data import sample_set  # noqa: E402
from test_pipeline import ddb, lam, s3, upload, wait_processed  # noqa: E402

DAY = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2026, 10, 22)
tf = os.environ.get("TERRAFORM", "terraform")
out = json.loads(subprocess.check_output([tf, f"-chdir={ROOT / 'envs/local'}", "output", "-json"]))["pipeline"]["value"]

files = sample_set(DAY)
files.append({"key": f"incoming/S05/export-{DAY}.csv", "body": b"id;store;total\n1;S05;10000\n", "store": "S05"})
t0 = time.time()
for f in files:
    f["etag"] = upload(out, f["key"], f["body"])
print(f"uploaded {len(files)} files to s3://{out['landing_bucket']}/incoming/")
print(f"\n{'file':<36}{'status':<10}{'ok':>6}{'bad':>5}  reason")
for f in files:
    e = wait_processed(out, f["key"], f["etag"])
    print(f"{f['key']:<36}{e['status']:<10}{e['rows_ok']:>6}{e['rows_bad']:>5}  {e.get('reason', '')}")
print(f"all processed in {time.time() - t0:.1f}s")

print(f"\ns3://{out['lake_bucket']}/")
for o in s3.list_objects_v2(Bucket=out["lake_bucket"])["Contents"]:
    if str(DAY) in o["Key"] or str(DAY).replace("-", "") in o["Key"]:
        print(f"  {o['Key']:<84}{o['Size']:>8} B")

fired = {"source": "aws.events", "detail-type": "Scheduled Event", "time": f"{DAY}T17:10:00Z"}
res = json.loads(lam.invoke(FunctionName=out["report_function"], Payload=json.dumps(fired))["Payload"].read())
print(f"\ndaily report (scheduled run at {fired['time']} = 00:10 WIB) -> {res['key']}")
print(json.dumps(res["sales"], indent=2))
