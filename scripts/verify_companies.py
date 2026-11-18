"""Verify ATS board tokens with real HTTP requests and write config/companies.yaml.

Each candidate (name, source, token) is requested once per listed source. Only tokens that return
HTTP 200 with a valid board payload AND at least `--min-jobs` India/remote jobs (per the ingest
location filter) are written. Every probe result is saved to config/companies_verification.json.

    python scripts/verify_companies.py
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
from app.ingest import ashby, greenhouse, lever  # noqa: E402
from app.ingest.http import PoliteClient  # noqa: E402
from app.ingest.normalize import location_matches  # noqa: E402

# Candidates: company display name -> sources to probe (token = key unless given as "source:token").
CANDIDATES: dict[str, list[str]] = {
    # India-HQ / large India engineering presence
    "PhonePe": ["greenhouse:phonepe"], "Groww": ["greenhouse:groww", "lever:groww"],
    "Razorpay": ["greenhouse:razorpaysoftwareprivatelimited", "lever:razorpay", "ashby:razorpay"],
    "CRED": ["lever:cred", "greenhouse:cred"], "Meesho": ["lever:meesho", "greenhouse:meesho"],
    "Postman": ["greenhouse:postman"], "BrowserStack": ["greenhouse:browserstack"],
    "Druva": ["greenhouse:druva"], "Innovaccer": ["greenhouse:innovaccer"], "Sprinklr": ["greenhouse:sprinklr"],
    "Mindtickle": ["lever:mindtickle", "greenhouse:mindtickle"], "HackerRank": ["greenhouse:hackerrank"],
    "Chargebee": ["lever:chargebee", "greenhouse:chargebee"], "Zeta": ["lever:zeta", "greenhouse:zeta"],
    "Jupiter": ["lever:jupiter"], "Smallcase": ["lever:smallcase"], "SpotDraft": ["lever:spotdraft", "ashby:spotdraft"],
    "Atlan": ["lever:atlan", "ashby:atlan"], "Hevo Data": ["lever:hevodata"], "Whatfix": ["lever:whatfix", "greenhouse:whatfix"],
    "MPL": ["lever:mpl"], "Upstox": ["lever:upstox", "greenhouse:upstox"], "Paytm": ["lever:paytm"],
    "InMobi": ["lever:inmobi", "greenhouse:inmobi"], "Glance": ["lever:glance", "greenhouse:glance"],
    "ShareChat": ["lever:sharechat", "greenhouse:sharechat"], "Dream11": ["lever:dream11", "greenhouse:dreamsports"],
    "Games24x7": ["lever:games24x7"], "Unacademy": ["lever:unacademy"], "Swiggy": ["lever:swiggy"],
    "Zepto": ["lever:zepto", "ashby:zepto"], "Rapido": ["lever:rapido"], "Dunzo": ["lever:dunzo"],
    "Plivo": ["lever:plivo", "greenhouse:plivo"], "Nium": ["lever:nium"], "NoBroker": ["lever:nobroker"],
    "Khatabook": ["lever:khatabook"], "CARS24": ["lever:cars24"], "Eightfold": ["lever:eightfold", "greenhouse:eightfold"],
    "Zenoti": ["lever:zenoti", "greenhouse:zenoti"], "Darwinbox": ["lever:darwinbox"], "Observe.AI": ["lever:observeai"],
    "HyperVerge": ["lever:hyperverge"], "ClearTax": ["lever:cleartax"], "FamPay": ["lever:fampay"],
    "BharatPe": ["lever:bharatpe"], "Cashfree": ["lever:cashfree"], "Pine Labs": ["lever:pinelabs"],
    "Perfios": ["lever:perfios"], "Navi": ["lever:navi"], "Acko": ["lever:acko"], "Juspay": ["lever:juspay"],
    "slice": ["lever:slice", "lever:sliceit"], "Fi Money": ["lever:epifi"], "Uni Cards": ["lever:uni"],
    "Sarvam AI": ["ashby:sarvam", "lever:sarvam"], "Rocketlane": ["ashby:rocketlane", "lever:rocketlane"],
    "Sprinto": ["ashby:sprinto", "lever:sprinto"], "Plum": ["ashby:plum", "lever:plum"],
    "Hasura": ["ashby:hasura", "greenhouse:hasura"], "Appsmith": ["ashby:appsmith", "lever:appsmith"],
    "ToolJet": ["ashby:tooljet", "lever:tooljet"], "Freshworks": ["greenhouse:freshworks", "lever:freshworks"],
    "Gojek": ["lever:gojek", "greenhouse:gojek"], "Ather Energy": ["lever:atherenergy"],
    "upGrad": ["lever:upgrad"], "Scaler": ["lever:scaler", "greenhouse:scaler"], "PhysicsWallah": ["lever:physicswallah"],
    "LeadSquared": ["lever:leadsquared"], "MoEngage": ["greenhouse:moengage", "lever:moengage"],
    "CleverTap": ["lever:clevertap", "greenhouse:clevertap"], "WebEngage": ["lever:webengage"],
    "Capillary": ["lever:capillary"], "Exotel": ["lever:exotel"], "Gupshup": ["lever:gupshup"],
    "Yellow.ai": ["lever:yellowai", "greenhouse:yellowai"], "Haptik": ["lever:haptik"], "Uniphore": ["greenhouse:uniphore", "lever:uniphore"],
    "Setu": ["lever:setu"], "M2P Fintech": ["lever:m2pfintech"], "Signzy": ["lever:signzy"], "Zluri": ["lever:zluri"],
    "Keka": ["lever:keka"], "Licious": ["lever:licious"], "Urban Company": ["lever:urbancompany"],
    "Lenskart": ["lever:lenskart"], "Nykaa": ["lever:nykaa"], "MakeMyTrip": ["lever:makemytrip"],
    "Ola": ["lever:olacabs"], "Krutrim": ["lever:krutrim", "ashby:krutrim"], "Simplilearn": ["lever:simplilearn"],
    "HighRadius": ["lever:highradius", "greenhouse:highradius"], "Toplyne": ["lever:toplyne", "ashby:toplyne"],
    # Global companies with India offices or remote roles
    "Stripe": ["greenhouse:stripe"], "Databricks": ["greenhouse:databricks"], "Cloudflare": ["greenhouse:cloudflare"],
    "GitLab": ["greenhouse:gitlab"], "Elastic": ["greenhouse:elastic"], "MongoDB": ["greenhouse:mongodb"],
    "Twilio": ["greenhouse:twilio"], "Okta": ["greenhouse:okta"], "Coinbase": ["greenhouse:coinbase"],
    "Dropbox": ["greenhouse:dropbox"], "Figma": ["greenhouse:figma"], "Reddit": ["greenhouse:reddit"],
    "Pinterest": ["greenhouse:pinterest"], "Airbnb": ["greenhouse:airbnb"], "Anthropic": ["greenhouse:anthropic"],
    "Vercel": ["greenhouse:vercel"], "Samsara": ["greenhouse:samsara"], "HashiCorp": ["greenhouse:hashicorp"],
    "Rubrik": ["greenhouse:rubrik"], "Pure Storage": ["greenhouse:purestorage"], "Datadog": ["greenhouse:datadog"],
    "New Relic": ["greenhouse:newrelic"], "PagerDuty": ["greenhouse:pagerduty"], "Grafana Labs": ["greenhouse:grafanalabs"],
    "Cockroach Labs": ["greenhouse:cockroachlabs"], "Yugabyte": ["greenhouse:yugabyte", "lever:yugabyte"],
    "Couchbase": ["greenhouse:couchbase"], "SingleStore": ["greenhouse:singlestore"], "ClickHouse": ["greenhouse:clickhouse"],
    "dbt Labs": ["greenhouse:dbtlabsinc"], "Fivetran": ["greenhouse:fivetran"], "Airbyte": ["ashby:airbyte", "greenhouse:airbyte"],
    "Toast": ["greenhouse:toast"], "Lyft": ["greenhouse:lyft"], "Instacart": ["greenhouse:instacart"],
    "Asana": ["greenhouse:asana"], "Airtable": ["greenhouse:airtable"], "Brex": ["greenhouse:brex"],
    "Affirm": ["greenhouse:affirm"], "Gusto": ["greenhouse:gusto"], "Canonical": ["greenhouse:canonical"],
    "Mozilla": ["greenhouse:mozilla"], "Wikimedia": ["greenhouse:wikimedia"], "Intercom": ["greenhouse:intercom"],
    "HubSpot": ["greenhouse:hubspot"], "Sumo Logic": ["greenhouse:sumologic"], "Cohesity": ["greenhouse:cohesity"],
    "Nutanix": ["greenhouse:nutanix"], "Netskope": ["greenhouse:netskope"], "Zscaler": ["greenhouse:zscaler"],
    "Wayfair": ["greenhouse:wayfair"], "Duolingo": ["greenhouse:duolingo"], "Coursera": ["greenhouse:coursera"],
    "Udemy": ["greenhouse:udemy"], "Thoughtworks": ["greenhouse:thoughtworks"], "Turing": ["greenhouse:turing", "lever:turing"],
    "Toptal": ["greenhouse:toptal"], "Andela": ["greenhouse:andela", "lever:andela"], "Automattic": ["greenhouse:automattic"],
    "DoorDash": ["greenhouse:doordash"], "Robinhood": ["greenhouse:robinhood"], "Discord": ["greenhouse:discord"],
    "Scale AI": ["greenhouse:scaleai"], "Celonis": ["greenhouse:celonis"], "Mattermost": ["greenhouse:mattermost", "lever:mattermost"],
    "Palantir": ["lever:palantir"], "Netflix": ["lever:netflix"], "Spotify": ["lever:spotify"],
    "Kraken": ["lever:kraken", "ashby:kraken"], "Weights & Biases": ["lever:wandb", "ashby:wandb"],
    "Outreach": ["lever:outreach"], "Anyscale": ["ashby:anyscale", "lever:anyscale"], "Binance": ["lever:binance"],
    "OpenAI": ["ashby:openai"], "Linear": ["ashby:linear"], "Ramp": ["ashby:ramp"], "Notion": ["ashby:notion"],
    "PostHog": ["ashby:posthog"], "Supabase": ["ashby:supabase"], "Deel": ["ashby:deel"], "Zapier": ["ashby:zapier"],
    "Perplexity": ["ashby:perplexity", "ashby:perplexity-ai"], "Cohere": ["ashby:cohere"], "Vanta": ["ashby:vanta"],
    "Retool": ["ashby:retool"], "Replit": ["ashby:replit"], "Modal": ["ashby:modal"], "Runway": ["ashby:runway"],
    "ElevenLabs": ["ashby:elevenlabs"], "LangChain": ["ashby:langchain"], "Pinecone": ["ashby:pinecone"],
    "Weaviate": ["ashby:weaviate"], "Qdrant": ["ashby:qdrant"], "Anysphere (Cursor)": ["ashby:anysphere", "ashby:cursor"],
    "Mercor": ["ashby:mercor"], "Composio": ["ashby:composio"], "Dagster Labs": ["ashby:dagster", "greenhouse:dagster"],
    "Prefect": ["ashby:prefect"], "Temporal": ["ashby:temporal", "greenhouse:temporal", "lever:temporal"],
    "Render": ["ashby:render"], "Railway": ["ashby:railway"], "Neon": ["ashby:neon"], "PlanetScale": ["ashby:planetscale"],
    "Clerk": ["ashby:clerk"], "Resend": ["ashby:resend"], "n8n": ["ashby:n8n"], "Astronomer": ["ashby:astronomer", "greenhouse:astronomer"],
    "Deepgram": ["ashby:deepgram"], "AssemblyAI": ["ashby:assemblyai"], "Synthesia": ["ashby:synthesia"],
    "Coder": ["ashby:coder", "greenhouse:coder"], "Sentry": ["ashby:sentry"], "Browserbase": ["ashby:browserbase"],
    "Hugging Face": ["ashby:huggingface"], "Docker": ["ashby:docker", "greenhouse:docker"], "Remote": ["greenhouse:remotecom", "ashby:remote"],
    "Multiverse": ["ashby:multiverse"], "Lightdash": ["ashby:lightdash"], "Cal.com": ["ashby:cal.com", "ashby:calcom"],
    "Trigger.dev": ["ashby:triggerdev", "ashby:trigger.dev"], "Inngest": ["ashby:inngest"], "Zolve": ["ashby:zolve", "lever:zolve"],
}

FETCH = {
    "greenhouse": lambda cl, t: cl.get_json(greenhouse.URL.format(token=t)),
    "lever": lambda cl, t: cl.get_json(lever.URL.format(token=t), {"mode": "json"}),
    "ashby": lambda cl, t: cl.get_json(ashby.URL.format(token=t)),
}


def jobs_of(source: str, data: object) -> list[dict] | None:
    if source == "lever":
        return data if isinstance(data, list) else None
    if isinstance(data, dict) and isinstance(data.get("jobs"), list):
        return [j for j in data["jobs"] if j.get("isListed", True)] if source == "ashby" else data["jobs"]
    return None


def loc_of(source: str, raw: dict) -> str | None:
    return {"greenhouse": greenhouse.location_of, "lever": lever.location_of, "ashby": ashby.location_of}[source](raw)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-jobs", type=int, default=1, help="min India/remote jobs to keep a company")
    args = ap.parse_args()
    s = get_settings()
    cl = PoliteClient(s.cfg.ingest.user_agent, rps=s.cfg.ingest.requests_per_second, timeout_s=15, retries=2)
    probes, kept = [], []
    for name, specs in CANDIDATES.items():
        for spec in specs:
            source, token = spec.split(":", 1)
            status, data = FETCH[source](cl, token)
            jobs = jobs_of(source, data) if status == 200 else None
            valid = jobs is not None
            ir = sum(1 for j in jobs or [] if location_matches(loc_of(source, j), s.cfg.ingest))
            india = sum(1 for j in jobs or [] if any(k in (loc_of(source, j) or "").lower() for k in s.cfg.ingest.india_keywords))
            rec = {"name": name, "source": source, "token": token, "status": status, "valid_payload": valid,
                   "total_jobs": len(jobs or []), "india_or_remote_jobs": ir, "india_jobs": india,
                   "checked_at": datetime.now(UTC).isoformat(timespec="seconds")}
            probes.append(rec)
            print(f"{status:>3} {source:<10} {token:<32} total={rec['total_jobs']:<4} india/remote={ir}", flush=True)
            if valid and ir >= args.min_jobs:
                kept.append(rec)
                break  # first working source for this company wins
    cl.close()
    (ROOT / "config" / "companies_verification.json").write_text(json.dumps(probes, indent=1), encoding="utf-8")
    kept.sort(key=lambda r: -r["india_or_remote_jobs"])
    entries = [{"name": r["name"], "source": r["source"], "token": r["token"],
                "country_hint": "IN" if r["india_jobs"] else "REMOTE"} for r in kept]
    header = (f"# Generated by scripts/verify_companies.py on {datetime.now(UTC).date()}.\n"
              "# Every token below returned HTTP 200 with a valid board payload and had India/remote jobs.\n"
              "# Full probe log (including failed tokens): config/companies_verification.json\n")
    (ROOT / "config" / "companies.yaml").write_text(header + yaml.safe_dump(entries, sort_keys=False), encoding="utf-8")
    print(f"\nprobed={len(probes)} ok200={sum(p['status'] == 200 and p['valid_payload'] for p in probes)} kept={len(kept)}")


if __name__ == "__main__":
    main()
