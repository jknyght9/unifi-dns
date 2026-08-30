#!/usr/bin/env bash
# Explore the UniFi DNS API on a live console and dump everything to ./probe-out/
#
# Usage:
#   1. UniFi console > Settings > Admins & Users > your admin > Control Plane API Key
#   2. echo 'UNIFI_API_KEY=xxxxx' > .env      (chmod 600 .env)
#   3. bash probe.sh
#
# Answers the open questions in API.md. Read-only: only GET requests.

set -uo pipefail

HOST="${UNIFI_HOST:-https://192.168.1.1}"
SITE="${UNIFI_SITE:-default}"
OUT="$(cd "$(dirname "$0")" && pwd)/probe-out"

[ -f "$(dirname "$0")/.env" ] && set -a && . "$(dirname "$0")/.env" && set +a

if [ -z "${UNIFI_API_KEY:-}" ]; then
  echo "UNIFI_API_KEY not set. Put it in .env next to this script." >&2
  exit 1
fi

mkdir -p "$OUT"
BASE="$HOST/proxy/network"

# get <name> <path>  -> writes $OUT/<name>.json, prints status
get() {
  local name="$1" path="$2" code
  code=$(curl -sk --max-time 15 \
    -H "X-API-Key: $UNIFI_API_KEY" -H "Accept: application/json" \
    -o "$OUT/$name.json" -w '%{http_code}' "$BASE$path")
  printf '%-34s %-4s %s\n' "$name" "$code" "$path"
  [ "$code" = "200" ]
}

jqq() { python3 -c "import sys,json;d=json.load(open(sys.argv[1]));$2" "$1" 2>/dev/null; }

echo "### Console: $HOST   Site: $SITE"
echo "### Output:  $OUT"
echo
echo "--- reachability -------------------------------------------------"
get sysinfo            "/api/s/$SITE/stat/sysinfo"
get self               "/api/s/$SITE/self"

echo
echo "--- integration API v1 -------------------------------------------"
if get sites "/integration/v1/sites"; then
  SITE_ID=$(jqq "$OUT/sites.json" \
    "print(next((s['id'] for s in d.get('data',[]) if s.get('internalReference')=='$SITE'), (d.get('data') or [{}])[0].get('id','')))")
  echo "resolved site UUID: ${SITE_ID:-<none>}"

  if [ -n "${SITE_ID:-}" ]; then
    get dns_policies_page1 "/integration/v1/sites/$SITE_ID/dns/policies?offset=0&limit=200"
    # does the filter DSL work here too?
    get dns_policies_filtered \
      "/integration/v1/sites/$SITE_ID/dns/policies?filter=$(python3 -c "import urllib.parse;print(urllib.parse.quote(\"type.eq('A_RECORD')\"))")"
    echo "$SITE_ID" > "$OUT/site_id.txt"
  fi
else
  echo "integration/v1 unavailable -> legacy only"
fi

echo
echo "--- legacy v2 API ------------------------------------------------"
get static_dns "/v2/api/site/$SITE/static-dns"

echo
echo "--- version / summary --------------------------------------------"
jqq "$OUT/sysinfo.json" "
i=(d.get('data') or [{}])[0]
print('network application:', i.get('version','?'))
print('udm firmware       :', i.get('udm_version') or i.get('ubnt_device_type','?'))
"

python3 - "$OUT" <<'PY'
import json, os, sys, collections
out = sys.argv[1]

def load(n):
    p = os.path.join(out, n)
    try:
        return json.load(open(p))
    except Exception:
        return None

new = load('dns_policies_page1.json')
if isinstance(new, dict) and 'data' in new:
    recs = new['data']
    print(f"\nintegration v1 records: {len(recs)} of {new.get('totalCount','?')} total")
    print("  by type:", dict(collections.Counter(r.get('type') for r in recs)))
    doms = collections.Counter('.'.join(r.get('domain','').split('.')[-2:]) for r in recs)
    print("  by apex:", dict(doms))
    if recs:
        print("  sample:", json.dumps(recs[0], indent=2))

filt = load('dns_policies_filtered.json')
if isinstance(filt, dict) and 'data' in filt:
    print(f"\nfilter DSL on dns/policies: WORKS ({filt.get('totalCount')} A records)")
elif filt is not None:
    print("\nfilter DSL on dns/policies: rejected ->", str(filt)[:200])

leg = load('static_dns.json')
if isinstance(leg, list):
    print(f"\nlegacy v2 records: {len(leg)}")
    print("  by type:", dict(collections.Counter(r.get('record_type') for r in leg)))
    if leg:
        print("  sample:", json.dumps(leg[0], indent=2))
elif isinstance(leg, dict) and 'data' in leg:
    print(f"\nlegacy v2 records: {len(leg['data'])} (wrapped in data envelope)")
    if leg['data']:
        print("  sample:", json.dumps(leg['data'][0], indent=2))
PY

echo
echo "Raw JSON in $OUT/"
