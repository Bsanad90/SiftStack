# DNC scrub vendors (research, 2026-10-05)

Which vendor should check a phone number against the Do Not Call lists before a dialer calls
it. The use case is a small real-estate investor cold calling homeowners (and heirs) in DC, MD
and VA. This is desk research from public pages and docs only. No account was opened, no
form, quote or demo request was sent, and no credentials were entered. Anything not shown on a
public page is marked **UNVERIFIED**. Nothing here is legal advice.

Footprint area codes: 202, 301, 240, 227, 410, 443, 667, 703, 571. **The list leaves out
771**, the DC overlay on 202 (in service since 2021). Add it if the SAN is scoped by area code.

**What we already have:** Trestle's `litigator_checks` add-on is on every Trestle call (My
Defaults, 2026-10-05). So litigator screening is already covered. The gap is the **federal
registry**, an **internal DNC list**, and optionally **reassigned numbers**. DataSift's own
`dnc` / `opt_out` fields count 0 on this account (see the CLAUDE.md traps), so nothing in the
CRM is doing this today.

## Two facts that shape the choice

1. **The SAN (Subscription Account Number) is yours, whichever vendor you use.** The FTC says
   every seller must subscribe to the registry itself. A scrub vendor may access the registry
   "on a seller's behalf" only through that seller's own SAN, and pays no extra fee for it
   ([FTC TSR Q&A](https://www.ftc.gov/business-guidance/resources/qa-telemarketers-sellers-about-dnc-provisions-tsr-0)).
   A vendor that does not ask for your SAN does not remove this duty.
2. **Heirs live everywhere.** DP batches dial relatives with out-of-state area codes. The SAN
   covers only the area codes you subscribe to. A number in an area code you have not bought
   cannot be scrubbed under your SAN. Calling it is a federal DNC exposure, and calling it
   into one of the 11 state-list states is also a state-list exposure (see below).

## FTC registry fees (brief)

- **FY2027 (from 2026-10-01):** first **5 area codes free**, then **$85 per area code** per
  year ($43 for the second half-year), capped at **$23,425** for the whole country
  ([Credit and Collection News, FY2027](https://www.creditandcollectionnews.com/ftc-raises-national-do-not-call-registry-access-fees-for-fy-2027/);
  FY2026 was $82 / $41 / $22,626, [FTC release](https://www.ftc.gov/news-events/news/press-releases/2025/08/telemarketer-fees-access-ftcs-national-do-not-call-registry-increase-2026)).
- **Footprint cost:** 10 codes (the 9 listed plus 771) = 5 free + 5 x $85 = **$425/yr**. With
  the 9 as listed: $340/yr.
- **31-day refresh:** a list must be re-scrubbed against a registry copy no older than 31 days
  ([FTC TSR Q&A](https://www.ftc.gov/business-guidance/resources/qa-telemarketers-sellers-about-dnc-provisions-tsr-0)).
  A record dialed weeks after its scrub needs a fresh one, so a per-dial API check is easier
  to defend than a one-time list scrub.
- Registration: telemarketing.donotcall.gov (NOT done; it is Basem's step).

## Comparison

"Own SAN" = whether the vendor's docs require your SAN for federal DNC.

| Vendor | API (single / batch) | Auth | Price (public) | Federal DNC | State DNC | Litigator | Wireless / reassigned | Internal DNC hosted |
|---|---|---|---|---|---|---|---|---|
| **DNC.com DNCScrub** (Contact Center Compliance, now Sonera) | REST, JSON or CSV; single GET + multi-number + batch/SFTP | `loginId` header API key, or OAuth2 bearer (24h) | **UNVERIFIED**: quote only, tiered, monthly or one-time | Yes, **own SAN** | Yes (GeoScrub, state rules, holidays, emergencies) | Yes | Wireless ID, VoIP ID, disconnect, **reassigned (FCC RND)** endpoints | **Yes** (+ EBR tracking) |
| **Blacklist Alliance** (Sonera since 2026-09) | REST GET `/lookup`, JSON; single only in OpenAPI spec, batch via portal | `key` query param | $300 / 10k one-time (~$0.03); $2,250 / 500k; plans from **$600/mo** (third-party) | Yes; results show "rejected due to SAN coverage", so **own SAN** (inferred) | Yes ("all" vs "select" states: inconsistent) | Yes (core product, "Litigation Firewall") | Wireless 0/1 flag; carrier (v3+); reassigned **UNVERIFIED** | Company DNC list: yes (per PropertyRadar doc) |
| **RealValidation** (RealPhoneValidation) DNC Lookup | REST GET/POST, JSON or XML; single number; bulk via portal file | `Token` param | **UNVERIFIED** (behind signup; 100 free lookups) | Yes, **own SAN** (SAN + Org ID + expiry entered first) | Yes ("all states") + DMA | **Yes, included free** | `iscell` Y/N/V; reassigned not offered | No (you keep your own) |
| **TCPA Litigator List** | REST; single `/scrub/phone/` + bulk `/scrub/phones/` (sync to 3,000, async + webhook above) | HTTP Basic, API key | Basic $199/mo (200k coins, no API); **API Silver $299/mo** (300k); API Gold $499/mo (500k) | Yes, **own SAN** (per NumberBroom) | Claimed all 50 in API doc; packages page silent: **UNVERIFIED** | Yes (core product) | `phone_type` line type; reassigned no | Custom suppression list: yes |
| **Tracerfy** DNC scrub (already a vendor here) | REST; batch `POST /v2/api/dnc/scrub/` (CSV), real-time `POST /v2/api/dnc/lookup/` (JSON, 1-15 phones) | Bearer (same key as skip trace) | **1 credit ($0.02) per phone batch, 5 credits ($0.10) real-time**, no subscription | Yes, but **no SAN asked for** | Yes | Yes | Line type only; reassigned no | No |
| Gryphon.ai (for scale only) | Real-time platform | n/a | **UNVERIFIED**; third-party reports ~$25k-$100k+/yr | Yes | Yes | Yes | Wireless + RND | Yes |

Not deep-dived but seen with public prices
([NumberBroom survey, 2026-09](https://numberbroom.com/dnc-scrubbing-cost)): The DNC Project
(federal from $35, states $99, litigator $129), DataZapp ($0.005/phone, $125 min), Landline
Remover. RealValidito (realvalidito.com) is a **different company** from RealValidation
despite the name. It publishes $0.01 to $0.0006 per DNC lookup, but its pages do not say
whether it needs your SAN or covers state or litigator lists.

## Per-vendor notes

### DNC.com: DNCScrub
- Product: https://www.dnc.com/dncscrub/. Docs: https://docs.dncscrub.com/ (index at
  `/llms.txt`).
- Single scrub: `GET https://www.dncscrub.com/app/main/rpc/scrub?phoneList=<10d>[|id|zip]&version=8&output=json`.
  It returns `ResultCode` (C clean, D DNC, W wireless, plus EBR codes), `Reason`,
  `LineType`, `CallingWindow`, `DoNotCallToday`, `IsCallAllowedATDS/NonATDS`, and
  `CarrierInfo` ([scrub-single](https://docs.dncscrub.com/api-reference/scrub/scrub-single.md)).
  Multi-number, litigator, reassigned (RND basic / Authority), internal DNC and state
  holiday/emergency endpoints are listed in the docs index.
- Auth: an API key in the `loginId` header, or OAuth2 with a 24h token
  ([auth](https://docs.dncscrub.com/api-reference/authentication.md)).
- It needs your SAN. Its own page notes the 5 free area codes.
- Pricing is tiered by volume and quote-only (sales@dnc.com, 866-362-5478): **UNVERIFIED**.
- It is the most complete product here. It is also the only one that hosts the internal DNC
  list, tracks EBR (established business relationship), and checks the FCC reassigned-number
  database behind one key.

### Blacklist Alliance
- Site: https://www.blacklistalliance.com/ (its pricing pages are `noindex`, and
  `/pricing` returned 404). API: `GET https://api.blacklistalliance.net/lookup?key=&phone=&ver=&resp=json`
  ([readme reference](https://blacklist-alliance.readme.io/reference/lookupsingle); legacy doc
  https://api.blacklistalliance.net/apidoc/index.html).
- Response `message` is one of Blacklisted / Good / Suppressed / FederalDNC / StateDNC. It
  also returns litigator codes (plaintiff / attorney, primary / secondary) and a wireless 0/1
  flag, plus carrier from v3.
- Prices come from NumberBroom's 2026-09-14 review, not from Blacklist Alliance:
  [vs page](https://numberbroom.com/vs/blacklist-alliance). That review notes the one-time
  blocks mention only the Litigation Firewall (the litigator screen), not the registry scrub,
  so it is **UNVERIFIED** whether $300 / 10k includes federal DNC.
- **Do not confuse it with tcpablacklist.com** ($80-$140/mo plans). That is a different site;
  its own FAQ says API access is on the Elite / Enterprise tiers only.
- PropertyRadar has a native integration
  ([help](https://help.propertyradar.com/en/articles/11084383-integrating-a-blacklist-alliance-account-in-propertyradar)).

### RealValidation: RealPhoneValidation DNC Lookup
- Product: https://realphonevalidation.com/do-not-call-list-scrubbing/. API doc:
  https://realphonevalidation.com/api-documentation/dnc-lookup-api-doc/
- `GET/POST https://api.realvalidation.com/rpvWebService/DNCLookup.php?phone=<10d>&token=<t>&output=json`.
  It returns `national_dnc`, `state_dnc`, `dma`, `litigator` (each Y / N / ?), `iscell`
  (Y / N / V), and `RESPONSECODE`. The recommended rate is at most 10 calls per second.
- **Requires your SAN, Org ID and SAN expiry** before first use. Litigator is "included at no
  additional charge". The internal DNC list stays on your side.
- Price is not public (pay-as-you-go, prepaid or enterprise; 100 free lookups after signup):
  **UNVERIFIED**.

### TCPA Litigator List
- API doc: https://tcpalitigatorlist.com/api-documentation/. Base URL
  `https://api.tcpalitigatorlist.com`, HTTP Basic auth, single scrub up to 50/s, bulk up to
  5/s, webhooks.
- Packages: https://tcpalitigatorlist.com/packages/. API tiers start at $299/mo. One coin = one
  number "in any combination of lists" (TCPA, troll, federal DNC, your suppression list).
  Overage is $0.0001-$0.002 per scrub.
- Its strength is litigator data, which Trestle already covers here. As a DNC scrubber the
  minimum spend is high for this volume.

### Tracerfy DNC scrub
- https://www.tracerfy.com/dnc-scrubbing and the API doc at
  https://www.tracerfy.com/real-estate-data-api. It checks federal, state, DMA and litigator
  for 1 credit ($0.02) per phone in batch, on the credit balance this repo already uses (see
  `skip-trace.md`). It is the cheapest and simplest option here.
- **Caveat:** no public page mentions a SAN, and no SAN is taken at signup. Under the FTC rule
  above, your SAN is still required. A vendor scrubbing from its own registry copy across many
  clients is not the arrangement the FTC Q&A describes. Treat its federal DNC flag as a useful
  signal, **not** as your registry compliance, until Tracerfy confirms in writing how its
  registry access works: **UNVERIFIED**.

### Gryphon.ai
- https://gryphon.ai/platform/. It is an enterprise, real-time compliance layer (federal,
  state, internal, RND, litigator). Pricing is quote-only; third parties report $25k+/yr
  ([SelectHub](https://www.selecthub.com/p/sales-acceleration-software/gryphon-ai/)). It is
  out of scale for this account.

### Smarter Contact (added 2026-10-05, at Basem's request)
- It is an SMS + power-dialer platform for REI teams, not a DNC scrub vendor. Its DNC scrub only
  exists inside the platform.
- **What is in the scrub.** Its "list verification" checks uploads against opt-outs, litigators,
  carrier blocks and "DNC registries" before a campaign launches. Verification is free on every
  plan, and "DNC & litigator scrub" is ticked on every tier
  ([pricing](https://smartercontact.com/pricing/),
  [DataSift integration](https://support.smartercontact.com/datasift-integration)).
- **Plans** (billed quarterly):
  - messaging $199 / $399 / $499 per month, Elite Plus $799–$1,799;
  - calling $199 / $399 / $499 per month.
- **Suppression lists** it keeps: Opted Out, DNC, Blocked for Compliance, Carrier Blocked,
  Undeliverable, Response Removal
  ([suppression lists](https://support.smartercontact.com/understanding-suppression-lists)).
  You can also upload your own DNC list as a CSV.
- **No public DNC API.** Scrub results are visible only inside the app; a "blocked numbers" CSV
  export exists per campaign
  ([export](https://smartercontact.com/help-center/exporting-blocked-numbers-from-campaign/)).
- **DataSift integration** runs both ways:
  - DataSift → Smarter Contact: contacts;
  - Smarter Contact → DataSift: dispositions, meaning property status, phone status, phone tags
    and notes, mapped from its labels.
  - Scrub results are **not** written back to DataSift.
- **UNVERIFIED:**
  - which state lists are covered;
  - whether the federal check runs under the customer's own SAN, which the FTC requires;
  - whether the scrub gates power-dialer calls as well as texts.

**Fit for this account.** It protects only the texts and dials that go out through Smarter Contact
itself. Basem's dialers work through smrtPhone, and Smarter Contact's scrub would not touch those
calls. It also can't feed `dnc_client`: there is no API, and the only output is a per-campaign CSV
of blocked numbers. That CSV could be imported as a secondary source (adds DNC hits only, never
clean) with a small CSV reader for `dnc_scrub.py`, which today harvests JSON only.

It is worth it only if texting or dialing moves to Smarter Contact. It does not replace the
SAN + vendor scrub for the smrtPhone dial sheets.

**DECISION (Basem, 2026-10-05): numbers will go out through Smarter Contact.** The CSV reader is
built: `dnc_scrub.py --sc-blocked <export.csv>`. It takes only DNC, opt-out, compliance and
litigator rows. A paid vendor is now needed only for numbers dialled outside Smarter Contact.

Ask Smarter Contact support:
1. Is the federal check run under our own SAN?
2. Which state lists does it cover?
3. Does the scrub gate power-dialer calls as well as texts?
4. What are the column names in the blocked export?

## State law: DC, Maryland, Virginia

**None of the three keeps its own DNC registry. All rely on the federal list.** The 11 state
lists are Colorado, Florida, Indiana, Louisiana, Massachusetts, Missouri, Oklahoma,
Pennsylvania, Tennessee, Texas and Wyoming; Mississippi dropped its list in 2023
([DNC.com FAQ](https://www.dnc.com/faq/state-do-not-call-lists?page=1)). State-list coverage
therefore matters only for **heirs and relatives with area codes in those 11 states**.

- **Maryland: Stop the Spam Calls Act of 2023** (HB 37 / SB 90, Ch. 413-414, in force
  2024-01-01). An automated system for selecting or dialing numbers, or a recorded voice,
  needs **prior express written consent**. Calls are allowed only **8 a.m. to 8 p.m.**
  called-party time. **No more than 3 calls or texts per 24 hours** on the same subject. No
  spoofing or hiding the caller's identity. A Maryland area code is presumed to reach a
  Maryland resident. Violations are enforceable under the MD Consumer Protection Act, with a
  private action. Sources: [Ch. 414 text](https://mgaleg.maryland.gov/2023rs/Chapters_noln/CH_414_hb0037t.pdf),
  [DWT summary](https://www.dwt.com/blogs/broadband-advisor/2023/05/telemarketing-tcpa-maryland-florida),
  [Manatt](https://www.manatt.com/insights/newsletters/tcpa-connect/state-law-updates-in-maryland,-west-virginia).
  The "automated system" wording is broader than the federal autodialer definition. Whether a
  power or predictive dialer such as SmrtPhone counts is an open legal question:
  **UNVERIFIED**.
- **Virginia: Telephone Privacy Protection Act** (Va. Code 59.1-510 to 59.1-518). Callers must
  honor the **federal registry**, scrubbed within 31 days (59.1-514). An internal stop-calling
  request must be honored **for 10 years**. Calls are allowed **8 a.m. to 9 p.m.** local time.
  The caller must give an identification and callback number. Private action: $500 for a first
  violation, $1,000 for a second, $5,000 for each later one, with more for willful violations
  (59.1-515). Exemptions are prior written permission, an EBR within 18 months, and a personal
  relationship. Source: [Va. Code ch. 44](https://law.lis.virginia.gov/vacodefull/title59.1/chapter44/).
- **District of Columbia:** no DC DNC registry was found in the DC Code. Some consumer sites
  claim a "DC No Call List"; that is **UNVERIFIED** and probably wrong. DC Code 34-1701 bans
  **automated dialing with a prerecorded message** for solicitation; live-agent calls are
  outside it ([DC Code](https://code.dccouncil.gov/us/dc/council/code/sections/34-1701)). DC
  Code 22-3226.01 et seq. (telephone solicitation fraud) has registration, surety-bond and
  disclosure sections and a private right of action
  ([subchapter](https://code.dccouncil.gov/us/dc/council/code/titles/22/chapters/32/subchapters/III-B)).
  Whether its registration applies to an investor calling to *buy* a house is **UNVERIFIED**;
  ask counsel.
- **Open question for all three:** whether a call offering to *buy* a homeowner's property is a
  "telephone solicitation" under the TSR, the TCPA and these state acts. Courts have split.
  Scrub as if it is.

## Recommendation

**Step zero, whatever the vendor:** Basem registers for his own SAN at
telemarketing.donotcall.gov and covers the 10 footprint codes ($425/yr at FY2027 rates). He
then decides whether out-of-state heir area codes are bought per code or simply never dialed
unscrubbed.

**Pick: RealValidation (RealPhoneValidation) DNC Lookup.**
- One documented REST call per number returns federal, state, DMA and litigator, plus cell
  flags.
- It runs under **your own SAN**, which is the arrangement the FTC rule describes.
- Litigator is included at no extra charge, so it doubles as a cross-check on Trestle.
- Pay-as-you-go suits a small, bursty dial volume.
- A per-number API fits the pipeline: scrub at build/push time and again inside the 31-day
  window.
- The open item is price: it is **UNVERIFIED**, and getting it needs a signup Basem has to
  approve.

**Runner-up: DNC.com DNCScrub.** It is the most complete and the most defensible product:
federal and state, wireless and **FCC reassigned numbers**, **hosted internal DNC** with the
10-year VA retention handled for you, EBR, and state holiday/emergency calling blocks. The
docs are clean (JSON, API key). It loses only because the price is quote-only and its tiers
are built for call centers. If the quote is reasonable it is the better long-term choice,
because the internal DNC list and RND are the two pieces no other small vendor here hosts.

**Not recommended as the compliance scrub:**
- **Tracerfy:** cheapest, already integrated, but there is no SAN in the loop. Use it as a
  pre-filter at most.
- **Blacklist Alliance and TCPA Litigator List:** their strength is litigator data, which
  Trestle already covers; registry coverage on their low tiers is unclear and the minimum
  spend is high.
- **Gryphon:** enterprise pricing.
