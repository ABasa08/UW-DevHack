# PerkPilot

**Buy it right. Fix it fast. Learn from every return.**

PerkPilot is an AI shopping and post-purchase assistant that installs into a Shopify store. It checks every AI-assisted purchase against the conditions the shopper explicitly approved, using Shopify's own totals. It reuses that approval as evidence when something goes wrong after the sale. And it turns confirmed returns into merchant-approved guidance that appears on the product page for the next shopper.

Built for DubHacks 2026, Visa track: *Reimagine Shopping*.

---

## The problem

AI shopping agents can misread requirements, misapply promotions, or ignore shipping and tax. Afterward it's hard to tell what the shopper actually approved. Returns systems know *what* was bought, but not the conditions behind it. Merchants also lack a safe way to turn a recurring problem into better guidance at the next checkout.

## The idea: one Verified Purchase Record

The shopper confirms a structured version of what they want. Deterministic Python checks enforce it against Shopify's real numbers. The approved purchase becomes a hash-chained record built from the paid Shopify order, and that record is reused:

| Stage | What happens | Who decides | Where it runs |
|---|---|---|---|
| Understand | Gemini (or the disclosed fallback interpreter) turns language into structured requirements | AI proposes | Storefront assistant (theme app embed) |
| Confirm | Shopper edits and confirms every essential field | Shopper | Storefront assistant |
| Recommend | Catalog search over the synced Shopify catalog; a shopping agent drafts a cart | Code + AI agent | PerkPilot API |
| **Verify** | Product, variant price, stock, the Shopify discount code, card-linked offer, budget and delivery date, in integer cents. Tax and the total come from Shopify's `draftOrderCalculate` for that exact cart | **Deterministic code + Shopify** | PerkPilot API + Shopify Admin API |
| Authorize | Shopper approves the exact charge; PerkPilot re-verifies, creates a Shopify draft order with exactly those lines, that code and that shipping line, and Shopify must total it to the cent | Shopper | Shopify checkout (the shopper pays there) |
| Record | The Verified Purchase Record is created from the paid Shopify order: total, card brand/last 4, fulfillments, refunds | Shopify facts | PerkPilot API |
| Resolve | The complaint is classified against the record and Shopify's fulfillment and payment data; remedies run as Shopify refunds, returns and free replacement orders | Code + shopper (+ merchant for real orders) | Storefront assistant + Shopify |
| Improve | Confirmed outcomes cross the thresholds and propose a rule; once approved it's written to the product's metafield | Merchant approves | Shopify admin (embedded app) + product page block |

**Core principle:** AI interprets language, deterministic code enforces rules, and humans approve. The model never approves payments, changes amounts, issues refunds or activates rules.

## What runs where

| Surface | What it is | Code |
|---|---|---|
| **Storefront assistant** | Theme app extension *app embed*: a floating "Shopping assistant" button on every page that opens the assistant (`/embed`). Signed-in customers are identified through a signed Shopify **app proxy** request | `extensions/perkpilot-theme/`, `frontend/app/(shop)/embed/` |
| **Product-page fit guidance** | Theme app *block* that shows merchant-approved size guidance for the selected size, read from the product metafield `perkpilot.fit_guidance` (with a "Switch to size L" action that never changes anything on its own) | `extensions/perkpilot-theme/blocks/fit-guidance.liquid` |
| **Checkout and payment** | Shopify's own checkout, via a draft order whose total Shopify calculated and PerkPilot verified. Inventory is reserved for 30 minutes. In a development store you pay with a Shopify test card | `backend/app/shopify_commerce.py` |
| **Merchant console** | Embedded Shopify admin app (App Bridge, session-token auth): dashboard, rule review, claims with approvals, orders/audit, setup, evaluation | `frontend/app/(admin)/app/` |
| **Webhooks** | Signed webhooks for products, inventory, orders, refunds, fulfillments, discount codes, uninstall and privacy topics; PerkPilot re-reads the canonical order from Shopify before acting | `backend/app/shopify_stream.py` |
| **Domain logic** | Verifier, pricing, records, claims, remedies, learning, metrics (Python) | `backend/app/` |

## Visa and payments

- **Verified payment intent:** the shopper approves the merchant, item and variant, Shopify-calculated price breakdown, final charge, payment choice and delivery promise. The approval event references the quote hash and the Shopify draft order.
- **Payment controls:** the maximum charge is enforced against Shopify's total. A future statement credit can never justify exceeding it. If Shopify's paid total differs from the approval (e.g. tax changed once an address was entered), the record is marked *not verified*, and a charge complaint is classified as a price mismatch with a partial-refund remedy for the difference.
- **Card-linked offer:** a **simulated issuer program** (no real Visa enrollment). Its eligibility is checked against the post-discount charge before checkout. After payment, PerkPilot checks that the Shopify order was actually paid with Visa, reading the card brand from the order's transaction details.
- **Card data:** shoppers enter cards only on Shopify's checkout. PerkPilot never sees card numbers. It reads the brand and last four digits from the Shopify order afterward.
- **Refunds:** issued through Shopify `refundCreate`. On Shopify **test orders** they run as soon as the shopper confirms; on real orders they wait for the merchant to approve in the admin. The remedy id is written into the refund note, so a retry finds the refund instead of issuing a second one.

PerkPilot does not implement, integrate with, or claim certification from Visa Intelligent Commerce or any Visa API.

## How Gemini is used

- Parsing shopping requests into a schema-constrained JSON object, validated by Pydantic, using the store's real categories, sizes and colors. Relative dates such as "Friday" are resolved by code in the store's timezone.
- Interpreting complaints into structured issues.
- Drafting the merchant summary of a proposed rule; it must restate the real counts and contain no other numbers.

Models: `gemini-3.5-flash` (low thinking), with `gemini-3.5-flash-lite` as backup, all within a 10-second budget. If no key is set, Gemini is unavailable, or its output fails validation, a deterministic rule-based interpreter runs instead, and the UI says so.

---

## Run it against your Shopify store

You need a Shopify development store (or a store you own), a **Dev Dashboard app** in the same organization, Python 3.10+, Node 18+, and a public HTTPS URL for this machine (an ngrok or Cloudflare tunnel to port 3000).

### 1. Configure the backend

Copy `backend/.env.example` to `backend/.env` and set:

```bash
PERKPILOT_MODE=shopify
SHOPIFY_SHOP=your-store.myshopify.com
SHOPIFY_CLIENT_ID=...          # Dev Dashboard -> your app -> Settings
SHOPIFY_CLIENT_SECRET=...
PERKPILOT_PUBLIC_URL=https://your-tunnel.ngrok-free.dev
PERKPILOT_ADMIN_TOKEN=...      # any long random string; for using the console outside Shopify admin
GEMINI_API_KEY=...             # optional
```

Shopify mode keeps its state in `backend/data/shopify_state.json` (or MongoDB if `MONGODB_URI` is set), separate from the demo store.

### 2. Start PerkPilot

```bash
./start.sh                        # backend on :8000, app on :3000
ngrok http --url=your-tunnel.ngrok-free.dev 3000
```

On first start in Shopify mode, the backend syncs your catalog and discount codes.

### 3. Deploy the Shopify app config and theme extension

From the repository root (Shopify CLI is pinned in `package.json`):

1. In `shopify.app.toml`, set `client_id` to your app's Client ID, and replace the tunnel host if yours differs (keep the paths).
2. Run `npm install`, then `npx shopify app deploy`. This releases the scopes, webhooks, app proxy, the embedded admin URL and the theme extension.
3. Open the app in your store's admin (**Apps → PerkPilot**) and approve the updated scopes.

### 4. Finish setup inside Shopify admin

Open **Apps → PerkPilot → Setup**:

- **Connect store** registers webhooks, backfills orders and resyncs the catalog and discount codes.
- **Turn on the assistant:** the link opens the theme editor at *App embeds*; enable **PerkPilot assistant**.
- **Add fit guidance:** the link adds the **PerkPilot fit guidance** block to your product template.
- **Prepare the judge demo:** pick a product and variant. PerkPilot uses Shopify's totals to create a discount code, set the card-offer threshold, and give you the exact shopper request to type. Optionally load labeled sample fit history for that product.

**Tunnels:** free ngrok domains put a "You are about to visit" page in front of browser traffic. Open your tunnel URL once in the same browser and click **Visit Site**; the cookie lasts 7 days and lets the storefront and admin iframes load in Chrome. A paid ngrok domain or a Cloudflare tunnel avoids this. Webhooks and the app proxy aren't affected.

**Test payments:** in a development store, enable Shopify's Bogus gateway or Shopify Payments test mode and pay with the test card `4242 4242 4242 4242` (any future expiry and CVC). Orders are flagged as test orders, which is what lets remedies run without merchant approval.

### Scopes PerkPilot uses

`read_products`, `write_products` (fit-guidance metafield), `read_inventory`, `read_orders`, `write_orders` (refunds, tags), `write_draft_orders` (Shopify-calculated totals and the verified checkout), `read_discounts`, `write_discounts` (the demo code), `read_fulfillments`, `write_fulfillments` and the merchant-managed fulfillment order scopes (test-order shipping helper), `read_returns`, `write_returns`.

---

## Modes

| `PERKPILOT_MODE` | Use it for |
|---|---|
| `shopify` | A real Shopify store: everything above. |
| `demo` (default) | The fictional fixture store used by the automated tests and the evaluation runner. Payments, refunds and shipping are simulated, and the assistant at `/embed` runs against it for local development. |
| `live` | Advisory only: no seeding, no commerce effects. Requires MongoDB. |

## Tests and verification

```bash
cd backend && python -m pytest -q                      # 50 tests
python scripts/check_shopify_queries.py                 # every GraphQL document vs Shopify's 2026-10 schema
cd ../frontend && npm run typecheck && npm run build
```

- **Spec acceptance tests** (all 16) run against the demo fixtures: correct product, wrong variant, budget, coupon-vs-card-offer threshold, expired promo, missed deadline, approval required, changed amount after approval, wrong delivery, ambiguous fit, exchange verification, duplicate remedy, rule approval gating, learning loop, persistence, reset.
- **Shopify-mode end-to-end tests** (`tests/test_shopify_mode.py`) run the whole judge flow against an in-memory Shopify (`tests/fake_shopify.py`):
  - the agent's mistake is blocked using Shopify-calculated tax
  - the draft order carries the exact code and lines
  - the record comes from the paid order (Visa •••• 4242)
  - fulfillment, the fit claim, a real replacement order, the rule proposal, and the metafield on approval
  - the next shopper sees the guidance

  They also cover: refunds issued once (idempotent through the refund note); real orders waiting for merchant approval; a Shopify price change after approval; tax changing at checkout; a wrong variant fulfilled; session isolation; app proxy signatures; admin session JWTs; and settings.
- **Schema validation:** `check_shopify_queries.py` validates every query and mutation against Shopify's published Admin API schema. With `--calls` (recorded by running the Shopify tests with `PERKPILOT_RECORD_SHOPIFY_CALLS=calls.json`) it also validates every variable PerkPilot sent against Shopify's input types.

### Evaluation (Merchant console → Evaluation)

Each run seeds an isolated in-memory copy of the fixture store, so a real store is never touched:

| Metric | Latest result |
|---|---|
| Checkout violation detection | 10/10 invalid carts blocked with the right rule |
| False rejections | 0/5 valid carts |
| Request parsing (field accuracy) | 26/26 with live Gemini 3.5 Flash, and 26/26 with the fallback interpreter (1 run each) |
| Claim classification | 9/9, including 3/3 appropriate abstentions |
| Remedy policy compliance | 5/5 |
| End-to-end workflow | 12/12 steps |
| Controlled comparison (held-out requests) | baseline 2/5 vs. approved rule 5/5 matching labels, constraints preserved 5/5 |

These numbers describe program behavior on synthetic fixtures. They do not show real-world return reduction.

## Judge demo

See [DEMO.md](DEMO.md) for the click-by-click script on your Shopify store.

## Known limitations

- Single store, USD. Variants are matched on Size and Color options.
- The card-linked offer is a simulated issuer program; Shopify has no card-offer enrollment data.
- The shopper can edit their shipping address on Shopify checkout, which can change tax. PerkPilot can't prevent that without a Shopify Plus checkout function; it detects it (the record is marked not verified) and offers the difference back.
- Learning thresholds are illustrative, not statistically validated. On a new store, the demo uses labeled synthetic sample history (shown as such everywhere).
- The "shopping agent" mistake comes from a deliberately naive planner that evaluates card-offer thresholds before discounts. That makes the demo repeatable; the verifier treats it like any untrusted proposal.
- The record hash detects changes relative to a trusted copy; it doesn't make storage tamper-proof by itself.
- Single-store install with the client-credentials grant; a public App Store app would add OAuth per shop and tenant scoping.

## Repository layout

```
shopify.app.toml               Shopify app config: scopes, webhooks, app proxy, embedded admin URL
extensions/perkpilot-theme/    Theme app extension: assistant app embed, fit-guidance block, storefront JS/CSS
backend/app/
  main.py                      FastAPI routes and mode wiring (demo / live / shopify)
  auth.py                      Shopper sessions, app proxy signatures, Shopify admin session tokens
  shopify_api.py               Admin GraphQL client and every document PerkPilot sends
  shopify_commerce.py          Totals, draft-order checkout, order reconciliation, refunds, exchanges, metafields, setup
  shopify_stream.py            Signed webhooks and the store overview
  shopify.py                   Catalog projection (python -m app.shopify sync)
  verifier.py, pricing.py      Deterministic checks and integer-cent pricing (Shopify tax when on Shopify)
  shopping.py, records.py      Intents, quotes, checkout handoff, Verified Purchase Records
  resolution.py, learning.py   Claims, remedies, outcomes, rule proposals
  metrics.py, evaluation.py    Dashboard metrics, sandboxed evaluation
  fixtures.py, demo.py         The fictional fixture store for tests and evaluation
backend/scripts/               check_shopify_queries.py
backend/tests/                 Acceptance, Shopify-mode end-to-end, webhook and adapter tests
frontend/app/(shop)/embed/     Storefront assistant: request, confirmation, verification, checkout, orders, claims
frontend/app/(admin)/app/      Embedded merchant console
shopify/perkpilot-embed.liquid Manual Custom Liquid install, for themes without the extension
```

[SHOPIFY_INTEGRATION_REPORT.md](SHOPIFY_INTEGRATION_REPORT.md) is the design review this integration was built from.
