# PerkPilot

**Buy it right. Fix it fast. Learn from every return.**

PerkPilot is an AI shopping and post-purchase assistant for a mid-sized footwear merchant. It checks every AI-assisted purchase against the conditions the shopper explicitly approved. It uses that same record to resolve problems after the sale, and it turns confirmed returns into merchant-approved guidance for the next shopper.

Built for DubHacks 2026, Visa track: *Reimagine Shopping*. The demo store, **StrideLab Running**, is fictional. Every payment, refund and shipment is simulated.

---

## The problem

AI shopping agents can misread requirements, misapply promotions or ignore shipping and tax. Afterward it can be hard to tell what the shopper actually approved. Returns systems know *what* was bought, but not the conditions behind it. Merchants also lack a safe way to turn a recurring problem into better guidance at the next checkout.

## The idea: one Verified Purchase Record

The shopper confirms a structured version of what they want. A deterministic Python verifier enforces it at checkout. The approved transaction is saved as a hash-chained record, and that record is reused:

| Stage | What happens | Who decides |
|---|---|---|
| Understand | Gemini (or the disclosed fallback interpreter) turns language into structured requirements | AI proposes |
| Confirm | Shopper edits and confirms every essential field | Shopper |
| Recommend | Catalog search; a shopping agent drafts a cart | Code + AI agent |
| **Verify** | Recomputes price, offers, budget, stock and delivery in integer cents; blocks violations; offers re-verified corrections | **Deterministic code** |
| Authorize | Shopper approves the exact charge; the server re-verifies at the moment of approval | Shopper |
| Resolve | The complaint is classified against the record, carrier and payment evidence; only eligible remedies are offered | Code + shopper |
| Improve | Confirmed outcomes cross demo thresholds and produce a proposed rule | Merchant approves |

**Core principle:** AI interprets language, deterministic code enforces rules, and humans approve. The model never approves payments, changes amounts, issues refunds or activates rules.

## What we built

- **Shopper app** (Next.js + Tailwind): storefront, product pages, AI assistant with an editable confirmation card, checkout verification with corrected options, purchase record with integrity checks, orders, and a resolution center.
- **Merchant console**: a dashboard computed from persisted events, rule review with evidence and small-sample warnings, approve/reject/pause controls, a claims list, and a sandboxed evaluation runner.
- **Backend** (FastAPI + Pydantic): verifier, pricing/offer engine, purchase records with SHA-256 hashes and an append-only hash-chained event log, claim classifier, policy-constrained remedy engine, exchange/refund simulator, learning engine, metrics and evaluation.
- **Persistence**: MongoDB when `MONGODB_URI` is set; otherwise a local JSON-file store that survives refreshes and restarts. The UI shows which one is active.

### How Gemini is used

- Parsing shopping requests into a schema-constrained JSON object (`responseSchema`), validated by Pydantic. Relative dates such as "Friday" are resolved by code against the demo clock in the store's timezone.
- Interpreting complaints into structured issues.
- Drafting the wording of proposed rules, checked so that both sizes appear and no new numbers are introduced.

Models: `gemini-3.5-flash` with low thinking (about 1.5s per call), with `gemini-3.5-flash-lite` as a backup when the primary is overloaded. On a 429/5xx the call retries once and then tries the backup, all within a 10-second budget. If no key is set, Gemini stays unavailable, or its output fails validation, a deterministic rule-based interpreter runs instead. The UI says so ("fallback interpreter"); it is never presented as live AI. The shopper-facing guidance sentence uses fixed wording; Gemini drafts only the merchant summary, which must restate the real counts and contain no other numbers.

### How deterministic verification works (`backend/app/verifier.py`, `pricing.py`)

Fixture order of operations: merchandise → flat demo tax (on the pre-coupon price) → shipping → coupons subtracted from merchandise → **charge**. Card-linked offers are evaluated against their declared basis (`final_charge`). They are *later statement credits* and never reduce the charge.

The checks are: product type, brand, color, size, stock, quantity, payment method, coupon validity/expiry/eligibility/minimum spend, offer stacking, card-offer enrollment and threshold, claimed charge, claimed savings, max charge, delivery deadline, plus non-blocking merchant-approved fit guidance. Each failure returns the rule, observed value, required value, explanation and corrected options that are themselves re-verified.

### How Visa concepts are represented

- **Verified payment intent:** the shopper approves the merchant, product/variant, full price breakdown, final charge, simulated payment method and delivery promise. That approval is stored as an event that references the quote hash.
- **Payment controls:** the max-charge limit is enforced against the actual charge. A future statement credit can never justify exceeding it. Card-linked offer eligibility depends on enrollment and on the post-discount charge.
- **Payment method:** "Visa Demo Card •••• 4242" is a labeled fixture, not a credential or token. No card numbers, CVVs or bank data are collected.
- **Post-purchase:** simulated refund to the original payment method, versus a clearly labeled *push-to-card payout concept*.

PerkPilot does not implement, integrate with, or claim certification from Visa Intelligent Commerce or any Visa API. These are application-layer demonstrations of the same trust ideas.

## What is simulated

Store, catalog, shoppers, prices, tax, coupons, card-linked offer, payment authorization and settlement, refunds, store credit, push-to-card, carrier events and the clock. Seeded history (26 orders, 3 fit claims, 5 edge-case orders for shopper Riley) is synthetic and labeled as seeded everywhere it appears.

---

## Run it

Requirements: **Python 3.10+** and **Node 18+**.

```bash
./start.sh
```

Or run the two servers by hand:

```bash
cd backend && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8000
```

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:3000. The frontend proxies `/api/*` to `http://127.0.0.1:8000` (override with `PERKPILOT_API_URL`). API docs are at http://localhost:8000/docs.

Optional: copy `backend/.env.example` to `backend/.env` and set `GEMINI_API_KEY` and/or `MONGODB_URI`.

### Tests

```bash
cd backend && .venv/bin/python -m pytest -q
```

There are 25 tests. They include all 16 required acceptance tests from the spec (correct product, wrong variant, budget, coupon-vs-card-offer threshold, expired promo, missed deadline, approval required, changed amount after approval, wrong delivery, ambiguous fit, exchange verification, duplicate remedy, rule approval gating, learning loop, persistence across restarts, demo reset). They also cover tamper detection, shopper isolation, missing-field handling and the sandboxed evaluation. Tests force the deterministic interpreter, so they never call a live model.

### Evaluation (Merchant console → Evaluation)

Each run seeds an isolated in-memory copy of the store, so live demo data is never touched. It reports exact counts:

| Metric | Latest result |
|---|---|
| Checkout violation detection | 10/10 invalid carts blocked with the right rule |
| False rejections | 0/5 valid carts |
| Request parsing (field accuracy) | 26/26 with live Gemini 3.5 Flash, and 26/26 with the fallback interpreter (1 run each) |
| Claim classification | 9/9, including 3/3 appropriate abstentions |
| Remedy policy compliance | 5/5 (duplicate acceptance, overcharge amount, ambiguous → review only, refund always available, sold-out exchange blocked) |
| End-to-end workflow | 12/12 steps |
| Controlled comparison (held-out requests) | baseline 2/5 vs. approved rule 5/5 matching labels, constraints preserved 5/5 |

These numbers describe program behavior on synthetic fixtures. They do not show real-world return reduction.

---

## Judge demo (about 4 minutes)

See [DEMO.md](DEMO.md) for the click-by-click script. In short:

1. **Catch the mistake:** the agent stacks STRIDE15 with a $10 card credit ("effective $100"). The verifier blocks it because the $110 charge is under the $120 threshold, and shows Option A ($110, passes) next to Option B ($125, over budget).
2. **Confirm:** approve the $110 simulated charge. The Verified Purchase Record is saved with its hash.
3. **Fix:** advance to delivery and report "These shoes feel too tight." The order matches what was delivered, and 2 prior confirmed fit exchanges exist, so it is a *suspected* fit issue. Exchange to 10.5 (re-verified, inventory reserved), deliver it, and confirm the fit.
4. **Learn:** that third confirmed outcome proposes a rule. The merchant reviews 3 outcomes among 21 orders with a small-sample warning and approves it.
5. **Prevent:** switch to Sam and make the same request. Guidance appears ("review size 10.5?"). Toggle it off and on to compare. Sam chooses 10.5; nothing changes silently.
6. **Dashboard:** blocked mistake, saved purchase, exchange, approved rule, changed recommendation.

Use **Reset** in the demo bar to replay. Use **Next delivery →** / **+1 day** to move the simulated clock.

## Known limitations

- Single merchant, footwear only, US sizes, USD. Tax is a flat per-product demo fixture.
- Learning thresholds (≥20 fulfilled orders, ≥3 confirmed outcomes, ≥2/3 agreeing direction) are illustrative, not statistically validated.
- The "shopping agent" mistake is produced by a deliberately naive planner that evaluates card-offer thresholds before discounts. That makes the demo repeatable; the verifier treats it like any untrusted proposal.
- No authentication: the persona switcher stands in for login. Endpoints still enforce shopper ownership of purchases and claims.
- The record hash detects changes relative to a trusted copy of the hash; it does not make storage tamper-proof by itself.
- Remedy "merchant value" ranking uses labeled demo assumptions. It only orders eligible options, and a cash refund always outranks equivalent store credit.

## Future work

Pilot with a real merchant's order and returns data, measure real return-rate and support-cost changes, add authenticated sessions and multi-merchant scoping, strengthen policy configuration, and investigate approved payment-network or PSP integrations for credentials, authorization and commerce signals where technically and commercially feasible.

## Repository layout

```
backend/app/
  main.py         FastAPI routes (spec §12 contracts plus a few read endpoints)
  models.py       Pydantic request/requirement schemas
  ai.py           Gemini calls with schema validation, and the disclosed fallback interpreter
  pricing.py      Integer-cent price breakdown, coupons, card-linked offers, delivery estimates
  verifier.py     Checkout rules, corrected options, the naive agent planner
  shopping.py     Intent parse/confirm, recommendations, quotes, checkout confirmation
  records.py      Verified Purchase Records, hashes, simulated shipments
  resolution.py   Claim classification, remedies, exchange/refund simulation, outcomes
  learning.py     Return-to-rule proposals, approve/reject/pause
  metrics.py      Dashboard numbers from persisted events
  evaluation.py   Sandboxed labeled-fixture evaluation runs
  fixtures.py     Seeded StrideLab data (deterministic IDs)
  store.py        MongoDB or local JSON-file persistence
backend/tests/    Acceptance tests
frontend/app/     Next.js App Router pages (shopper + /merchant)
```
