"""Find more boards with fresher roles (interns, new grads, 0-2 years) in India or remote.

Every candidate token is requested once per listed source. A company is added to
config/companies.yaml only if the token returns HTTP 200 with a valid board payload AND the board has
at least --min-fresher jobs that are both India/remote (ingest location filter) and fresher-relevant
(app/ingest/seniority.py). Existing entries are kept untouched. Every probe is appended to
config/companies_verification.json.

    python scripts/verify_more_companies.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import ROOT, get_settings  # noqa: E402
from app.ingest.http import PoliteClient  # noqa: E402
from app.ingest.normalize import location_matches  # noqa: E402
from app.ingest.role_family import TECH_FAMILIES, classify_role  # noqa: E402
from app.ingest.seniority import classify  # noqa: E402
from scripts.verify_companies import FETCH, jobs_of, loc_of  # noqa: E402

CANDIDATES: dict[str, list[str]] = {
    # Indian startups / scale-ups known to post intern and early-career roles
    "Udaan": ["lever:udaan"], "Pocket FM": ["lever:pocketfm", "ashby:pocketfm"], "Kuku FM": ["lever:kukufm"],
    "Zupee": ["lever:zupee"], "WinZO": ["lever:winzo"], "Spinny": ["lever:spinny"], "ixigo": ["lever:ixigo"],
    "Delhivery": ["lever:delhivery"], "Shiprocket": ["lever:shiprocket"], "Rupeek": ["lever:rupeek"],
    "Masai School": ["lever:masaischool"], "Newton School": ["lever:newtonschool"], "HackerEarth": ["lever:hackerearth"],
    "Coding Ninjas": ["lever:codingninjas"], "INDmoney": ["lever:indmoney"], "CoinDCX": ["lever:coindcx"],
    "CoinSwitch": ["lever:coinswitch"], "Zetwerk": ["lever:zetwerk"], "Moglix": ["lever:moglix"],
    "Porter": ["lever:porter"], "BlackBuck": ["lever:blackbuck"], "HealthifyMe": ["lever:healthifyme"],
    "Practo": ["lever:practo"], "PharmEasy": ["lever:pharmeasy"], "Cult.fit": ["lever:curefit"],
    "Pristyn Care": ["lever:pristyncare"], "PolicyBazaar": ["lever:policybazaar"], "Locus": ["lever:locus"],
    "Sigmoid": ["lever:sigmoid"], "Tiger Analytics": ["lever:tigeranalytics"], "Quantiphi": ["lever:quantiphi"],
    "MathCo": ["lever:themathcompany"], "Gameskraft": ["lever:gameskraft"], "Jar": ["lever:jar", "ashby:jar"],
    "Ultrahuman": ["lever:ultrahuman", "ashby:ultrahuman"], "Tekion": ["lever:tekion", "greenhouse:tekion"],
    "Classplus": ["lever:classplus"], "Leap": ["lever:leap"], "Eloelo": ["lever:eloelo"],
    "Cleartrip": ["lever:cleartrip"], "OYO": ["lever:oyo", "greenhouse:oyorooms"], "Fyle": ["lever:fyle"],
    "Wingify": ["lever:wingify"], "Freshworks": ["greenhouse:freshworksinc"], "BrowserStack": ["lever:browserstack"],
    "Postman": ["lever:postman"], "Innovaccer": ["lever:innovaccer"], "Sprinklr": ["lever:sprinklr"],
    "ThoughtSpot": ["greenhouse:thoughtspot", "lever:thoughtspot"], "Harness": ["greenhouse:harnessinc", "lever:harness"],
    "Icertis": ["lever:icertis", "greenhouse:icertis"], "Commvault": ["greenhouse:commvault"],
    "Mad Street Den": ["lever:madstreetden"], "Signeasy": ["lever:signeasy"], "Facilio": ["lever:facilio"],
    "Exotel": ["greenhouse:exotel"], "Kissflow": ["lever:kissflow"], "Chargebee": ["greenhouse:chargebeeinc"],
    "Uplers": ["lever:uplers"], "Trell": ["lever:trell"], "Bizongo": ["lever:bizongo"], "Vedantu": ["lever:vedantu"],
    "Nobroker": ["greenhouse:nobroker"], "Dukaan": ["lever:dukaan"], "BharatX": ["ashby:bharatx", "lever:bharatx"],
    "Plivo": ["greenhouse:plivoinc"], "Hasura": ["lever:hasura"], "SuperOps": ["lever:superops", "ashby:superops"],
    "Atlys": ["ashby:atlys", "lever:atlys"], "Smallest AI": ["ashby:smallest", "ashby:smallestai"],
    "Composio": ["ashby:composio"], "Ema": ["ashby:ema", "greenhouse:ema"], "Neysa": ["ashby:neysa", "lever:neysa"],
    # Global companies with India early-career or remote intern programs
    "Rippling": ["ashby:rippling", "greenhouse:rippling"], "Uber": ["greenhouse:uber"], "Atlassian": ["greenhouse:atlassian"],
    "Gong": ["greenhouse:gongio"], "Mixpanel": ["greenhouse:mixpanel"], "Amplitude": ["greenhouse:amplitude"],
    "Braze": ["greenhouse:braze"], "Agoda": ["greenhouse:agoda"], "Grab": ["greenhouse:grab"],
    "Tower Research": ["greenhouse:towerresearchcapital"], "IMC Trading": ["greenhouse:imc"],
    "Hudson River Trading": ["greenhouse:hudsonrivertrading"], "Optiver": ["greenhouse:optiver"],
    "Jane Street": ["greenhouse:janestreet"], "Citadel": ["greenhouse:citadel"], "DE Shaw": ["greenhouse:deshaw"],
    "Graviton": ["lever:gravitonresearchcapital"], "Quadeye": ["lever:quadeye"], "Arcesium": ["greenhouse:arcesium"],
    "Snowflake": ["ashby:snowflake", "greenhouse:snowflake"], "Confluent": ["ashby:confluent", "greenhouse:confluent"],
    "Netlify": ["greenhouse:netlify"], "DigitalOcean": ["greenhouse:digitalocean98", "greenhouse:digitalocean"],
    "Sourcegraph": ["greenhouse:sourcegraph91", "ashby:sourcegraph"], "Tailscale": ["greenhouse:tailscale"],
    "Redis": ["greenhouse:redislabs"], "Kong": ["greenhouse:kong"], "Postman (GH)": ["greenhouse:postmanlabs"],
    "Okta (Auth0)": ["greenhouse:auth0"], "Wiz": ["greenhouse:wizinc"], "Glean": ["greenhouse:gleanwork"],
    "Sigma Computing": ["greenhouse:sigmacomputing"], "Hightouch": ["greenhouse:hightouch"],
    "Airwallex": ["greenhouse:airwallex", "ashby:airwallex"], "Wise": ["greenhouse:wise"], "Revolut": ["greenhouse:revolut"],
    "Checkout.com": ["greenhouse:checkoutcom"], "Coupang": ["greenhouse:coupang"], "Toptal (Lever)": ["lever:toptal"],
    "Crossover": ["lever:crossover"], "Doist": ["greenhouse:doist"], "Zeta Global": ["greenhouse:zetaglobal"],
    "Navan": ["greenhouse:tripactions", "greenhouse:navan"], "Plaid": ["lever:plaid"], "Chime": ["greenhouse:chime"],
    "Nubank": ["greenhouse:nubank"], "Mercari": ["greenhouse:mercari"], "Canva": ["lever:canva"],
    "Freshpaint": ["ashby:freshpaint"], "Vapi": ["ashby:vapi"], "Cartesia": ["ashby:cartesia"],
    "Hebbia": ["ashby:hebbia"], "Harvey": ["ashby:harvey"], "Writer": ["ashby:writer"], "Clay": ["ashby:claylabs", "ashby:clay"],
    "Sierra": ["ashby:sierra"], "Decagon": ["ashby:decagon"], "Lovable": ["ashby:lovable"], "Bolt (StackBlitz)": ["ashby:stackblitz"],
    "Mistral AI": ["lever:mistral"], "Together AI": ["greenhouse:togetherai", "ashby:together"],
    "Fireworks AI": ["greenhouse:fireworksai"], "Groq": ["greenhouse:groq"], "Scale AI (Ashby)": ["ashby:scale"],
    "Turing (Lever)": ["lever:turing"], "Outlier": ["greenhouse:outlier", "ashby:outlier"], "Handshake": ["ashby:handshake"],
}


# Round 3: India engineering boards. First group: tokens seen in real public posting URLs (web search,
# 2026-11-19); the rest are candidates. Lever slugs can be case-sensitive, so variants are tried.
CANDIDATES_R3: dict[str, list[str]] = {
    "Enterpret": ["greenhouse:enterpret"], "CloudSEK": ["greenhouse:cloudsek"], "100ms": ["lever:100ms"],
    "Fi (epiFi)": ["lever:epifi"], "Stable Money": ["lever:stable-money1", "lever:stable-money"],
    "Acceldata": ["lever:acceldata"], "Sprinto (Lever)": ["lever:Sprinto"], "zAIMLer": ["lever:zaimler"],
    "ConverseNow": ["lever:ConverseNow", "lever:conversenow"], "ValGenesis": ["lever:valgenesis"],
    "Loop AI": ["lever:Loop AI", "lever:loopai"], "AiDash": ["greenhouse:aidash", "lever:aidash", "ashby:aidash"],
    "Asha Health": ["ashby:asha-health", "ashby:ashahealth", "greenhouse:ashahealth"], "Kulfi": ["ashby:kulfi", "lever:kulfi"],
    "Apna": ["lever:apna", "greenhouse:apna"], "SymphonyAI": ["greenhouse:symphonyai", "lever:symphonyai"],
    "Cohesity": ["greenhouse:cohesity", "lever:cohesity"], "Inflection.io": ["lever:inflection", "ashby:inflection"],
    "Magnit": ["greenhouse:magnit", "lever:magnit"],
    # India engineering centres / startups (candidates)
    "Media.net": ["greenhouse:medianet", "lever:medianet"], "Nutanix": ["greenhouse:nutanix", "lever:nutanix"],
    "Gupshup": ["greenhouse:gupshup"], "LambdaTest": ["lever:lambdatest", "greenhouse:lambdatest"],
    "Testsigma": ["lever:testsigma"], "Procore": ["greenhouse:procore"], "Infoblox": ["greenhouse:infoblox"],
    "Neo4j": ["greenhouse:neo4j"], "Dremio": ["greenhouse:dremio"], "Starburst": ["greenhouse:starburst"],
    "JFrog": ["greenhouse:jfrog"], "ChargePoint": ["greenhouse:chargepoint"], "PayU": ["greenhouse:payu", "lever:payu"],
    "Careem": ["greenhouse:careem"], "Delivery Hero": ["greenhouse:deliveryhero"], "Rapyd": ["greenhouse:rapyd"],
    "Adyen": ["greenhouse:adyen"], "Leena AI": ["lever:leenaai", "ashby:leena"], "Wadhwani AI": ["lever:wadhwaniai"],
    "Sigmoid": ["greenhouse:sigmoid"], "Tredence": ["greenhouse:tredence"], "Quizizz": ["lever:quizizz", "greenhouse:quizizz"],
    "Hotstar": ["greenhouse:hotstar", "lever:hotstar"], "Myntra": ["greenhouse:myntra"], "Swiggy (GH)": ["greenhouse:swiggy"],
    "Zomato (GH)": ["greenhouse:zomato"], "PhonePe (Lever)": ["lever:phonepe"], "Groww (Lever)": ["lever:groww"],
    "Unacademy (GH)": ["greenhouse:unacademy"], "upGrad (GH)": ["greenhouse:upgrad"], "Licious (GH)": ["greenhouse:licious"],
    "Urban Company (GH)": ["greenhouse:urbancompany"], "Uber India": ["greenhouse:uberindia"], "Atlassian (Lever)": ["lever:atlassian"],
    "Rippling (GH)": ["greenhouse:rippling"], "Visa (GH)": ["greenhouse:visa"], "Mastercard (GH)": ["greenhouse:mastercard"],
    "Expedia (GH)": ["greenhouse:expedia"], "Wix": ["greenhouse:wix"], "monday.com": ["greenhouse:mondaycom", "ashby:monday"],
    "Freshworks (Lever)": ["lever:freshworks"], "Zeta (GH)": ["greenhouse:zetasuite"], "Juspay (GH)": ["greenhouse:juspay"],
    "Slice (GH)": ["greenhouse:slice"], "Jupiter (GH)": ["greenhouse:jupiter"], "CRED (GH)": ["greenhouse:dreamplug"],
    "BrowserStack (GH)": ["greenhouse:browserstackinc"], "Postman (Ashby)": ["ashby:postman"], "Hasura (GH)": ["greenhouse:hasurainc"],
    "Atlan (Lever)": ["lever:atlan-2"], "Fyllo": ["lever:fyllo"], "Dezerv": ["lever:dezerv", "ashby:dezerv"],
    "Jar (Lever)": ["lever:jar-app", "lever:changejarapp"], "Zolve (Ashby)": ["ashby:zolve-1"], "Even Healthcare": ["lever:even", "ashby:even"],
    "Pine Labs (GH)": ["greenhouse:pinelabs"], "Razorpay (Lever)": ["lever:razorpay-2"], "Ultrahuman (GH)": ["greenhouse:ultrahuman"],
    "Hevo (GH)": ["greenhouse:hevodata"], "DevRev": ["ashby:devrev", "greenhouse:devrev", "lever:devrev"],
    "Sarvam (Lever)": ["lever:sarvam-ai"], "Gan.ai": ["ashby:gan", "lever:ganai"], "Rocketlane (GH)": ["greenhouse:rocketlane"],
    "Neysa (GH)": ["greenhouse:neysa"], "Simplismart": ["ashby:simplismart", "lever:simplismart"], "Portkey": ["ashby:portkey"],
    "Supermemory": ["ashby:supermemory"], "Pipeshift": ["ashby:pipeshift"], "Lyzr": ["ashby:lyzr", "lever:lyzr"],
    "Bhanzu": ["lever:bhanzu", "ashby:bhanzu"], "Scaler (Ashby)": ["ashby:scaler"], "Ola Krutrim (GH)": ["greenhouse:krutrim"],
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-fresher", type=int, default=1)
    ap.add_argument("--round", type=int, default=2, help="2 = fresher boards, 3 = India engineering boards (tech roles only)")
    args = ap.parse_args()
    s = get_settings()
    existing = yaml.safe_load((ROOT / "config" / "companies.yaml").read_text(encoding="utf-8")) or []
    have = {(e["source"], e["token"]) for e in existing} | {e["name"].lower() for e in existing}
    cl = PoliteClient(s.cfg.ingest.user_agent, rps=s.cfg.ingest.requests_per_second, timeout_s=15, retries=2)
    probes, kept = [], []
    candidates = CANDIDATES_R3 if args.round == 3 else CANDIDATES
    tried = {(r["source"], r["token"]) for r in json.loads((ROOT / "config" / "companies_verification.json").read_text(encoding="utf-8"))}
    for name, specs in candidates.items():
        if name.lower() in have:
            continue
        for spec in specs:
            source, token = spec.split(":", 1)
            if (source, token) in have:
                break
            if (source, token) in tried:
                continue
            status, data = FETCH[source](cl, token)
            jobs = jobs_of(source, data) if status == 200 else None
            ir = [j for j in jobs or [] if location_matches(loc_of(source, j), s.cfg.ingest)]

            def title(j: dict, src: str = source) -> str:
                return str(j.get("text") if src == "lever" else j.get("title") or "")

            def desc(j: dict) -> str:
                return str(j.get("descriptionPlain") or "")  # Greenhouse list has no content: title-only

            fresh = [j for j in ir if classify(title(j), desc(j))[0]
                     and (args.round < 3 or classify_role(title(j))[0] in TECH_FAMILIES)]
            india = sum(1 for j in fresh if any(k in (loc_of(source, j) or "").lower() for k in s.cfg.ingest.india_keywords))
            rec = {"name": name, "source": source, "token": token, "status": status, "valid_payload": jobs is not None,
                   "total_jobs": len(jobs or []), "india_or_remote_jobs": len(ir), "fresher_india_or_remote_jobs": len(fresh),
                   "fresher_india_jobs": india, "checked_at": datetime.now(UTC).isoformat(timespec="seconds"), "round": args.round}
            probes.append(rec)
            print(f"{status:>3} {source:<10} {token:<28} total={rec['total_jobs']:<4} india/remote={len(ir):<4} fresher={len(fresh)}",
                  flush=True)
            if jobs is not None and len(fresh) >= args.min_fresher:
                kept.append(rec)
                break
    cl.close()
    vpath = ROOT / "config" / "companies_verification.json"
    old = json.loads(vpath.read_text(encoding="utf-8")) if vpath.exists() else []
    vpath.write_text(json.dumps(old + probes, indent=1), encoding="utf-8")
    kept.sort(key=lambda r: -r["fresher_india_or_remote_jobs"])
    added = [{"name": r["name"], "source": r["source"], "token": r["token"],
              "country_hint": "IN" if r["fresher_india_jobs"] else "REMOTE"} for r in kept]
    text = (ROOT / "config" / "companies.yaml").read_text(encoding="utf-8").rstrip("\n")
    if added:
        what = "tech-family (engineering/data_ml/product) " if args.round == 3 else ""
        text += (f"\n# Round {args.round} ({datetime.now(UTC).date()}, scripts/verify_more_companies.py): HTTP 200 and >= "
                 f"{args.min_fresher} fresher-relevant {what}India/remote job(s).\n"
                 + yaml.safe_dump(added, sort_keys=False))
    (ROOT / "config" / "companies.yaml").write_text(text.rstrip("\n") + "\n", encoding="utf-8")
    ok = sum(p["status"] == 200 and p["valid_payload"] for p in probes)
    print(f"\nprobed={len(probes)} ok200={ok} added={len(added)} fresher_jobs_on_added_boards="
          f"{sum(r['fresher_india_or_remote_jobs'] for r in kept)}")


if __name__ == "__main__":
    main()
