#!/usr/bin/env bash
# End-to-end check: migrate, then drive a full record lifecycle through the API
# against the live gateway, including a rollback. Creates and removes records
# under *.claude.invalid only.
set -euo pipefail
cd "$(dirname "$0")"
export $(grep -v '^#' .env | xargs)
export DATABASE_URL="postgresql+asyncpg://unifidns:${POSTGRES_PASSWORD}@localhost:${POSTGRES_PORT:-5433}/unifidns"

echo "== migrate =="
(cd backend && ../.venv/bin/alembic upgrade head)

echo "== start api =="
./.venv/bin/uvicorn app.main:app --app-dir backend --port 8000 --log-level warning &
API=$!
trap 'kill $API 2>/dev/null || true' EXIT
for i in $(seq 1 30); do
  curl -sf http://localhost:8000/api/health >/dev/null 2>&1 && break
  sleep 1
done

j() { python3 -c "import sys,json;d=json.load(sys.stdin);print(eval(sys.argv[1]))" "$1"; }

echo "== status =="
curl -s localhost:8000/api/system/status | python3 -m json.tool

echo "== declare apex =="
curl -s -X POST localhost:8000/api/apexes -H 'Content-Type: application/json' \
  -d '{"name":"claude.invalid"}' | python3 -m json.tool

echo "== create A record =="
CS=$(curl -s -X POST localhost:8000/api/records -H 'Content-Type: application/json' -d '{
  "record":{"type":"A_RECORD","domain":"verify.claude.invalid","ipv4Address":"10.99.99.50","ttlSeconds":300},
  "note":"verify.sh smoke test"}')
echo "$CS" | python3 -m json.tool | head -20
RID=$(echo "$CS" | j "d['revisions'][0]['unifi_id']")
echo "record id: $RID"

echo "== zones (grouping) =="
curl -s localhost:8000/api/records | python3 -c "
import sys,json
d=json.load(sys.stdin)
for z in d['zones']:
    print(f\"  {z['apex']} [{z['count']}]\")
    for r in z['records']: print(f\"     {r['label']:14} {r['type']:12} {r['value']}\")
"

echo "== update =="
UCS=$(curl -s -X PUT "localhost:8000/api/records/$RID" -H 'Content-Type: application/json' -d '{
  "record":{"type":"A_RECORD","domain":"verify.claude.invalid","ipv4Address":"10.99.99.51","ttlSeconds":600}}')
echo "$UCS" | j "d['summary']+' -> '+d['status']"
UCSID=$(echo "$UCS" | j "d['id']")

echo "== rollback plan (dry run) =="
curl -s -X POST "localhost:8000/api/changesets/$UCSID/rollback?dry_run=true" | python3 -m json.tool

echo "== apply rollback =="
curl -s -X POST "localhost:8000/api/changesets/$UCSID/rollback?dry_run=false" | j "d['summary']+' -> '+d['status']"

echo "== value after rollback (expect 10.99.99.50) =="
curl -s localhost:8000/api/records | python3 -c "
import sys,json
for z in json.load(sys.stdin)['zones']:
    for r in z['records']:
        if 'verify' in r['fqdn']: print('   ', r['fqdn'], '=', r['value'], 'ttl', r['ttl_seconds'])
"

echo "== drift =="
curl -s localhost:8000/api/drift | j "'clean='+str(d['clean'])"

echo "== history =="
curl -s localhost:8000/api/changesets | python3 -c "
import sys,json
for cs in json.load(sys.stdin): print(f\"   {cs['status']:8} {cs['source']:9} {cs['summary']}\")
"

echo "== cleanup =="
curl -s -X DELETE "localhost:8000/api/records/$RID" | j "d['status']"
curl -s localhost:8000/api/records | j "'records remaining: '+str(d['total'])"
echo "OK"
