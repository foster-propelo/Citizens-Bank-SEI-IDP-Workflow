import argparse
import json
import os
import sys

import requests

BASE = "https://app.harness.io/gateway/sei/api"
ACCOUNT_ID = "Zs_twTkwSduwBfXlfGQylw"
API_KEY = os.environ["SEI_API_KEY"] # pipeline secret

HEADERS = {"x-api-key": API_KEY, "harness-account": ACCOUNT_ID, "Content-Type": "application/json"}
PARAMS = {"orgIdentifier": "cfg_enterprise", "projectIdentifier": "SEI_SEI_00000"}

# arg name -> (entity key, filter property, integration type)
# integration type None = use FIXED_IDS instead of looking the integration up
FIELDS = {
    "project": ("im_general", "im_project", "IM"),
    "target_branch": ("cd_services_pipelines", "cd_branch", "CD"),
    "pipeline": ("cd_services_pipelines", "cd_pipeline_name", "CD"),
    "business_service": ("im_incident_detection", "itsm_business_service", None),
}
FIXED_IDS = {"business_service": "14"}  # TODO: make dynamic



def parse_values(raw):
    """Flags may repeat. Each value may be a JSON array ["a","b"], a bracketed list
    like [a, b] or ['a', 'b'] (how a list can render as text), or a plain a,b string."""
    out = []
    for item in raw or []:
        item = item.strip()
        parts = None
        if item.startswith("[") and item.endswith("]"):
            try:
                parts = json.loads(item)
            except ValueError:
                parts = item[1:-1].split(",")
        if parts is None:
            parts = item.split(",")
        for x in parts:
            x = str(x).strip().strip("\"'").strip()
            if x and x not in out:
                out.append(x)
    return out
 
 
p = argparse.ArgumentParser()
p.add_argument("--team-ref-id", type=int, required=True)
p.add_argument("--project", action="append")
p.add_argument("--pipeline", action="append")
p.add_argument("--target-branch", action="append")
p.add_argument("--business-service", action="append")
args = p.parse_args()
VALUES = {field: parse_values(getattr(args, field)) for field in FIELDS}
print(f"Raw values received: { {f: getattr(args, f) for f in FIELDS} }")
 
 
def update_team(team):
    print(f"=== SEI team update: team {team}, account {ACCOUNT_ID}, "
          f"org {PARAMS['orgIdentifier']}, project {PARAMS['projectIdentifier']} ===")
    print("Requested: " + " ".join(f"{f}={VALUES[f]!r}" for f in FIELDS))
    print(f"API key: {'set' if API_KEY else 'EMPTY'} (length {len(API_KEY)})")
 
    print(f"[1] GET current settings: {BASE}/v2/team-settings/teams/{team}")
    settings = requests.get(f"{BASE}/v2/team-settings/teams/{team}", headers=HEADERS, params=PARAMS)
    print(f"    HTTP {settings.status_code}")
    if not settings.ok:
        print(f"    GET failed. Response body (first 500 chars): {settings.text[:500]}")
        print("    Hint: an HTML sign-in page or 401 means the token was not accepted; "
              "check the SEI_API_KEY secret.")
    settings.raise_for_status()
    entities = (settings.json().get("override") or {}).get("entities") or []
    has_override = bool(entities)  # POST creates the override once, PATCH after that
    print(f"    Existing override entities: {[e.get('entityKey') for e in entities] or 'none'}")
    print(f"    Team has override: {has_override} -> will use {'PATCH' if has_override else 'POST first, then PATCH'}")
 
    def expressions(f):
        return f.get("expressions") or [f]  # composite has "expressions"; simple is itself
 
    # group requested fields by entity: several properties on one entity become one AND filter
    groups = {}
    for field, (entity, prop, itype) in FIELDS.items():
        if not VALUES[field]:
            print(f"[skip] {field}: no value passed")
            continue
        groups.setdefault(entity, []).append((field, prop, itype))
 
    integrations = None
    results = []
    for entity, items in groups.items():
        label = "+".join(f for f, _, _ in items)
        wanted = {prop: VALUES[field] for field, prop, _ in items}
        itype = items[0][2]
        print(f"[{label}] entity={entity} properties={wanted!r} type={itype}")
 
        # integration IDs already holding a filter on this entity for the team
        existing = {
            iid: f
            for e in entities if e.get("entityKey") == entity
            for iid, f in (e.get("integrationFilters") or {}).items()
            if f  # integrationFilters can hold null entries
        }
 
        fixed = next((FIXED_IDS[f] for f, _, _ in items if f in FIXED_IDS), None)
        if existing:
            iid = next(iter(existing))
            print(f"    Found existing filter on integration {iid}: "
                  f"{[(x.get('property'), x.get('values')) for x in expressions(existing[iid])]} "
                  f"-> reusing that integration ID")
        elif fixed:
            iid = fixed
            print(f"    Using fixed integration {iid} (not looked up yet)")
        else:
            print(f"    No existing filter for {list(wanted)}; looking up the team's {itype} integration")
            if integrations is None:
                r = requests.get(f"{BASE}/v2/teams/{team}/integrations", headers=HEADERS, params=PARAMS)
                print(f"    GET integrations: HTTP {r.status_code}")
                if not r.ok:
                    print(f"    GET integrations failed. Response body (first 500 chars): {r.text[:500]}")
                r.raise_for_status()
                data = r.json()
                integrations = data.get("integrations", []) if isinstance(data, dict) else data
                print(f"    Team integrations: "
                      f"{[(i.get('id'), i.get('integrationType'), i.get('name')) for i in integrations]}")
            if not any(i.get("integrationType") == itype for i in integrations):
                print(f"    ERROR: team {team} has no {itype} integration. "
                      f"Integration types present: {sorted({str(i.get('integrationType')) for i in integrations})}")
            iid = str(next(i["id"] for i in integrations if i.get("integrationType") == itype))
            print(f"    Using {itype} integration {iid}")
 
        # requested properties, plus any other property already on the existing filter (kept as is)
        exprs = [
            {"type": "simple", "property": prop, "operator": "EQUALS", "values": values}
            for prop, values in wanted.items()
        ]
        for x in expressions(existing[iid]) if existing else []:
            if x.get("property") and x["property"] not in wanted:
                print(f"    Keeping existing {x['property']}={x.get('values')}")
                exprs.append({"type": "simple", "property": x["property"],
                              "operator": x.get("operator", "EQUALS"), "values": x.get("values")})
 
        if len(exprs) == 1:
            flt = {"useDefaults": False, **exprs[0]}
        else:
            flt = {"useDefaults": False, "type": "composite", "operator": "AND", "expressions": exprs}
        entity_body = {
            "entityKey": entity,
            "entityType": entity,
            "integrationFilters": {iid: flt},
        }
        entity_body["useDefaults"] = False  # any change turns defaults off
 
        method = "PATCH" if has_override else "POST"
        url = f"{BASE}/v2/team-settings/overrides/teams/{team}"
        print(f"    {method} {url}")
        print(f"    Body: {json.dumps({'entities': [entity_body]})}")
        r = requests.request(
            method,
            url,
            headers=HEADERS,
            params=PARAMS,
            json={"entities": [entity_body]},
        )
        print(f"{label}: {method} integration {iid} -> {wanted}: HTTP {r.status_code}")
        if not r.ok:
            print(r.text)
            print(f"    FAILED {method} for {label}. Request URL: {r.request.url}")
            if r.status_code == 409:
                print("    Hint: 409 Conflict. The override may already exist (POST vs PATCH mismatch).")
            elif r.status_code in (401, 403):
                print("    Hint: auth problem. Check the SEI_API_KEY secret and its permissions.")
            elif r.status_code == 400:
                print("    Hint: 400 Bad Request. Check the body above (filter property, integration ID, values).")
            elif r.status_code == 404:
                print("    Hint: 404. Check the team ref ID and the org/project identifiers.")
            results.append((label, method, iid, wanted, f"FAILED HTTP {r.status_code}"))
            print("=== Summary so far ===")
            for res in results:
                print(f"    {res[0]}: {res[1]} integration {res[2]} -> {res[3]} : {res[4]}")
        r.raise_for_status()
        print(f"    OK: {label} set to {wanted} on integration {iid}")
        results.append((label, method, iid, wanted, f"OK HTTP {r.status_code}"))
        has_override = True
 
    print("=== Summary ===")
    if not results:
        print("    Nothing to do: no values were passed.")
    for res in results:
        print(f"    {res[0]}: {res[1]} integration {res[2]} -> {res[3]} : {res[4]}")
    print(f"=== Done: team {team} ===")
 
 
# ---- find other teams with the exact same name ----
selected = args.team_ref_id
targets = [selected]
search_failed = False
 
print(f"=== Looking for teams with the same name as team {selected} ===")
r = requests.post(
    f"{BASE}/v2/teams/list",
    headers=HEADERS,
    params={**PARAMS, "page": 0, "pageSize": 5000},
    json={},
)
print(f"    POST teams/list: HTTP {r.status_code}")
if not r.ok:
    print(f"    Team search failed. Response body (first 500 chars): {r.text[:500]}")
    print("    Continuing with the selected team only.")
    search_failed = True
else:
    all_teams = r.json().get("items") or []
    print(f"    Teams returned: {len(all_teams)}")
    me = next((t for t in all_teams if t.get("refId") == selected), None)
    if me is None:
        print(f"    Team {selected} was not in the list; continuing with the selected team only.")
    else:
        tree_id = (me.get("orgTree") or {}).get("id")
        print(f"    Selected team: name={me.get('name')!r} leaf={me.get('leaf')} "
              f"orgTree={(me.get('orgTree') or {}).get('name')!r} (id {tree_id})")
        matches = [
            t for t in all_teams
            if t.get("name") == me.get("name")
            and t.get("refId") != selected
        ]
        if matches:
            print(f"    Found {len(matches)} other team(s) named {me.get('name')!r}:")
            for t in matches:
                print(f"        refId {t.get('refId')} (parentRefId {t.get('parentRefId')}, "
                      f"leaf={t.get('leaf')}, orgTree={(t.get('orgTree') or {}).get('name')!r})")
        else:
            print(f"    No other teams named {me.get('name')!r}.")
        targets += [t["refId"] for t in matches]
 
failed = []
for n, t in enumerate(targets, 1):
    print(f"\n########## Team {t} ({n} of {len(targets)}) ##########")
    try:
        update_team(t)
    except Exception as e:
        print(f"!!! Team {t} failed: {type(e).__name__}: {e}")
        failed.append(t)
 
print("\n########## Overall ##########")
print(f"    Teams processed: {targets}")
print(f"    Succeeded: {[t for t in targets if t not in failed]}")
print(f"    Failed: {failed or 'none'}")
if search_failed:
    print("    Note: the same-name search failed, so only the selected team was updated.")
if failed or search_failed:
    sys.exit(1)
