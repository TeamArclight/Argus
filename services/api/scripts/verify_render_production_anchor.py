"""Production verification script for a BRAND NEW audit event on Polygon Amoy testnet."""

import json
import time
import urllib.request
import sys

base_url = "https://argus-api-h93c.onrender.com"
auth_url = "https://argus-topaz-nine.vercel.app/api/auth/dev-token"

print("[Step 0] Acquiring fresh procurement officer token from Vercel...")
token_req = urllib.request.Request(
    auth_url,
    data=json.dumps({"email": "demo.procurement@argus.local", "password": "ArgusDemo2026!"}).encode("utf-8"),
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(token_req, timeout=15) as r:
    auth_data = json.loads(r.read())
    tok = auth_data["token"]
    print(f"Acquired token for: {auth_data['principal']['name']} ({auth_data['principal']['role']})")


# 1. Trigger compliance evaluation for bidder_bharat_03
target_bidder = "bidder_bharat_03"
print(f"[Step 1] Triggering normal compliance evaluation for {target_bidder} on Render...")
verify_req = urllib.request.Request(
    f"{base_url}/api/v1/bidders/{target_bidder}/verify",
    data=b"",
    headers={
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": f"prod-verify-bharat-{int(time.time())}",
    },
)

try:
    with urllib.request.urlopen(verify_req, timeout=45) as r:
        job = json.loads(r.read())
        print(f"Verification Job: ID={job.get('id')}, Status={job.get('status')}, Type={job.get('job_type')}")
except urllib.error.HTTPError as e:
    sys.stderr.write(f"Verification trigger error: {e.code} - {e.read().decode()}\n")
    sys.exit(1)

# 2. Fetch the newly created COMPLIANCE_EVALUATION_COMPLETED audit event
print("\n[Step 2] Fetching newly created COMPLIANCE_EVALUATION_COMPLETED audit event...")
events_req = urllib.request.Request(
    f"{base_url}/api/v1/audit/events?action=COMPLIANCE_EVALUATION_COMPLETED&limit=1",
    headers={"Authorization": f"Bearer {tok}"},
)
with urllib.request.urlopen(events_req, timeout=15) as r:
    events = json.loads(r.read())
    if not events:
        sys.stderr.write("No audit events found!\n")
        sys.exit(1)
    ev = events[0]
    event_id = ev.get("id")
    print(f"New Event ID:       {event_id}")
    print(f"Action:             {ev.get('action')}")
    print(f"Entity ID:          {ev.get('entity_id')}")
    print(f"Timestamp:          {ev.get('timestamp')}")
    print(f"Canonical Hash:     {ev.get('event_hash')}")
    print(f"Initial Status:     {ev.get('blockchain_status')}")

# 3. Monitor for worker sweep, submission, and confirmation
print("\n[Step 3] Monitoring for inline worker pickup and confirmation on Polygon Amoy...")
confirmed_data = None
for attempt in range(25):
    time.sleep(4)
    detail_req = urllib.request.Request(
        f"{base_url}/api/v1/audit/{event_id}/blockchain",
        headers={"Authorization": f"Bearer {tok}"},
    )
    try:
        with urllib.request.urlopen(detail_req, timeout=15) as r:
            bc_data = json.loads(r.read())
            status = bc_data.get("blockchain_status")
            tx = bc_data.get("transaction_hash")
            block = bc_data.get("block_number")
            err = bc_data.get("blockchain_error")
            print(f"Poll {attempt+1:02d}: Status = {status}, Tx = {tx}, Block = {block}, Error = {err}")
            if status == "CONFIRMED":
                confirmed_data = bc_data
                break
            if status == "FAILED" and err:
                print(f"Event failed on attempt {attempt+1}: {err}")
    except Exception as poll_err:
        print(f"Poll {attempt+1:02d} error: {poll_err}")

if not confirmed_data:
    sys.stderr.write(f"\n[FAIL] Event {event_id} did not reach CONFIRMED within timeout.\n")
    sys.exit(1)

print("\n" + "=" * 65)
print(" PRODUCTION AUDIT EVENT ANCHORED SUCCESSFULLY ON POLYGON AMOY!")
print("=" * 65)
print(f"Event ID:          {event_id}")
print(f"Canonical Hash:    {confirmed_data.get('event_hash')}")
print(f"Transaction Hash:  {confirmed_data.get('transaction_hash')}")
print(f"Block Number:      {confirmed_data.get('block_number')}")
print(f"Network:           {confirmed_data.get('network')}")
print(f"Explorer URL:      {confirmed_data.get('explorer_url')}")
print("-" * 65)

# 4. Authoritative Verification Call
print("\n[Step 4] Calling POST /api/v1/audit/{event_id}/verify on Render...")
verify_api_req = urllib.request.Request(
    f"{base_url}/api/v1/audit/{event_id}/verify",
    data=b"",
    headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
)
with urllib.request.urlopen(verify_api_req, timeout=30) as r:
    verify_resp = json.loads(r.read())
    print("Verification Response:")
    print(json.dumps(verify_resp, indent=2))

    assert verify_resp.get("integrity") == "VERIFIED", f"Expected VERIFIED, got {verify_resp.get('integrity')}"
    assert verify_resp.get("computed_hash") == verify_resp.get("onchain_hash"), "Hash mismatch!"
    print("\n[SUCCESS] Production API Authoritative Verification Confirmed: INTEGRITY VERIFIED")
