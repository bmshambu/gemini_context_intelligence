# Context Intelligence — Shopping Companion (Gemini Enterprise)

A single Gemini Enterprise agent (ADK → Vertex AI Agent Engine → GE chat) with a
**two-tier persona memory** driving a personalized 5-step e-commerce checkout.
Markdown output for now (A2UI later). See [DESIGN.md](DESIGN.md) for rationale.

```
Onboarding → country/language/gender/age/interests            (PERMANENT memory)
Step 1  What would you like to buy?  → picks filtered by profile, priced in your currency
Step 2  Shipping address
Step 3  Payment (cash on delivery / card)
Step 4  Order summary → type "confirm"   (dummy, no real payment)
Step 5  Done — order id                                        (in-progress = 3-DAY memory)
```

Leave at step 2/3 and come back → the opener hints to **resume where you left off**.

## Memory Management — what the demo covers

The whole demo is a study in **what to remember, for how long, and where**. Three
scopes: **Permanent** (who you are — never expires), **Temporary** (what you're
doing now — 3-day TTL), and **Transient** (this session only). Deterministic data
(catalogue/prices) isn't memory but is listed for the full picture.

| Capability | Scope | Lifespan | Shown in |
|---|---|---|---|
| Profile: country → **currency**, language, gender, age | 🟢 Permanent | Never expires | Conv 1 |
| **Preferred name / nickname** (asked at onboarding if email name is long/undecodable) | 🟢 Permanent | Never | Conv 1 |
| **Interests** (add / change anytime — partial update, rest kept) | 🟢 Permanent | Never | Conv 2 |
| **Currency override** (see prices in USD even though country = India) | 🟢 Permanent | Never | Conv 2 |
| **Country change** → recomputes currency | 🟢 Permanent | Never | Conv 2 |
| **Usual shipping address** (offered on repeat orders, overridable) | 🟢 Permanent | Never | repeat order |
| **Usual payment method** (offered on repeat orders, overridable) | 🟢 Permanent | Never | repeat order |
| **In-progress order** (5 steps: product → address → payment → confirm → done) | 🟡 Temporary | 3-day TTL | Conv 1 |
| **Resume where you left off** (return mid-checkout) | 🟡 Temporary | 3-day TTL; cleared on confirm | Conv 1 |
| **Shopping for someone else** (kid / wife → their gender, age, interests) | 🔵 Transient | This session; cleared on confirm or "for myself" | Conv 3 |
| **Recommendations** (filter by demographics, rank by interests ⭐, currency prices, emojis) | ⚪ Deterministic | — (from catalogue) | Conv 1–3 |
| **"clear memory"** demo reset (wipes both tiers, re-onboards) | ⚪ Utility | — | between runs |

### The three scopes at a glance

|  | 🟢 Permanent | 🟡 Temporary | 🔵 Transient |
|---|---|---|---|
| **Stores** | who you are | your open order | who you're shopping *for* now |
| **Where** | Memory Bank | Memory Bank | ADK session state |
| **Keyed by** | user_id | user_id | the live conversation |
| **Cross-session?** | ✅ forever | ✅ for 3 days | ❌ this chat only |
| **Written to Memory Bank?** | yes | yes (with TTL) | no |
| **Example** | country, currency, interests, nickname | product + address + payment mid-checkout | "shopping for my kid" |

**One-line thesis for the demo:** the agent separates *who you are* (permanent) from
*what you're doing right now* (temporary) and *who you're shopping for this moment*
(transient) — and never lets one overwrite another.

## Demo script (stakeholder walkthrough)

Two conversations to run live. **Deterministic data, LLM delivery:** the numbers
below — every price, currency conversion, ⭐ pick, ordering, and resume fact — are
the agent's real, exact output. The surrounding **wording is illustrative**: the
LLM phrases each message freshly (and in the shopper's language), so the greeting
and framing will read a little differently each run while the data stays identical.

### Conversation 1 — permanent + temporary memory (across two sessions)

**Session A — first visit (nothing known yet)**

**🧑 Shopper:** Hi

**🤖 Companion:**
> ## 🛍️ Welcome!
> Let's set up your shopping profile so I can personalize prices and picks. Please tell me:
> - **Country** (sets your currency) · **Language** · **Gender** · **Age** · **Any interests** (optional)

**🧑 Shopper:** India, English, male, 30, into fitness and tech

> 💾 **Permanent memory written** — profile saved: country **India** → currency **INR ₹**, gender male, age 30 (adult), interests fitness + tech.

**🤖 Companion:**
> ### 🛍️ Step 1 of 5 — What would you like to buy?
> Prices in **INR**. Picks based on your profile (⭐ = matches your interests):
> 1. ⌚ **Smartwatch (Fitness+)** — ₹12,367 ⭐ _fitness, tech_
> 2. 🧘 **Yoga Mat (Eco Cork)** — ₹3,237 ⭐ _fitness_
> 3. 👟 **Running Shoes (Lightweight)** — ₹7,387 ⭐ _fitness_
> 4. 🎧 **Wireless Noise-Cancelling Headphones** — ₹16,517 ⭐ _tech_
> 5. 🎲 **Board Game Night Bundle** — ₹4,897
> 6. 🍳 **Cast-Iron Cookware Set** — ₹9,960

**🧑 Shopper:** number 1

**🤖 Companion:** Great — **Smartwatch (Fitness+)** (₹12,367). **Step 2 of 5** — what's your shipping address?

**🧑 Shopper:** 42 MG Road, Bengaluru 560001

**🤖 Companion:** Saved. **Step 3 of 5** — how would you like to pay: **cash on delivery** or **card**?

> 💤 _Shopper closes the chat here — mid-checkout at step 3._
> 🕒 **Temporary memory holds the cart** (product + address + step), with a **3-day TTL**.

---

**Session B — returns later (same profile, same device)**

**🧑 Shopper:** Hi

**🤖 Companion:**
> ## 👋 Welcome back!
> You have an order in progress — **Smartwatch (Fitness+)** (₹12,367).
> You left off at **step 3 of 5 — choose payment method**.
> Reply **continue** to pick up where you left off, or **start over** for a new order.

> ✅ No re-onboarding — **permanent memory** recognized the shopper. **Temporary memory** restored the exact cart + step.

**🧑 Shopper:** continue → card

**🤖 Companion:**
> 🧾 **Order summary**
> - 📦 Smartwatch (Fitness+) — ₹12,367
> - 🏠 Ship to: 42 MG Road, Bengaluru 560001
> - 💳 Payment: Card
>
> Type **confirm** to place the order.

**🧑 Shopper:** confirm

**🤖 Companion:** 🎉 Order placed! Your order id is **ORD-482193**. (Demo — no real payment taken.)

---

### Conversation 2 — changing interests / currency / country mid-flow

Returning shopper (profile: **India**, INR, interests fitness + tech). The opener shows the INR list from Conversation 1. Then they change their mind three times — each change updates **permanent** memory in place and re-prices instantly, **without losing the rest of the profile**.

**🧑 Shopper:** Actually, show me prices in USD

> 💾 **Permanent memory updated** — currency → **USD $** only. Country stays **India**; gender/age/interests untouched.

**🤖 Companion:**
> Sure — prices now in **USD** 💵:
> 1. ⌚ **Smartwatch (Fitness+)** — $149 ⭐ _fitness, tech_
> 2. 🧘 **Yoga Mat (Eco Cork)** — $39 ⭐ _fitness_
> 3. 👟 **Running Shoes (Lightweight)** — $89 ⭐ _fitness_
> 4. 🎧 **Wireless Noise-Cancelling Headphones** — $199 ⭐ _tech_
> 5. 🎲 **Board Game Night Bundle** — $59
> 6. 🍳 **Cast-Iron Cookware Set** — $120

**🧑 Shopper:** I'm more into cooking now than fitness

> 💾 **Permanent memory updated** — interests → **cooking, tech**. Recommendations re-rank.

**🤖 Companion:**
> Updated your picks ✨:
> 1. 🍳 **Cast-Iron Cookware Set** — $120 ⭐ _cooking_
> 2. ⌚ **Smartwatch (Fitness+)** — $149 ⭐ _tech_
> 3. 🎧 **Wireless Noise-Cancelling Headphones** — $199 ⭐ _tech_
> 4. 🧘 **Yoga Mat (Eco Cork)** — $39
> 5. 🎲 **Board Game Night Bundle** — $59
> 6. 👟 **Running Shoes (Lightweight)** — $89

**🧑 Shopper:** I'm relocating — change my country to the UK

> 💾 **Permanent memory updated** — country → **United Kingdom**; currency recomputes to **GBP £** (a country change resets currency to that country's default unless a currency is named).

**🤖 Companion:**
> Welcome to the UK 🇬🇧 — prices now in **GBP**:
> 1. 🍳 **Cast-Iron Cookware Set** — £95 ⭐ _cooking_
> 2. ⌚ **Smartwatch (Fitness+)** — £118 ⭐ _tech_
> 3. 🎧 **Wireless Noise-Cancelling Headphones** — £157 ⭐ _tech_
> 4. 🧘 **Yoga Mat (Eco Cork)** — £31
> 5. 🎲 **Board Game Night Bundle** — £47
> 6. 👟 **Running Shoes (Lightweight)** — £70

**🧑 Shopper:** the cookware set → 10 Downing St, London → card → confirm

**🤖 Companion:** 🎉 Order placed! **Cast-Iron Cookware Set** (£95), order id **ORD-591027**.

> **Takeaway:** every mid-flow change edited **one field** of permanent memory and kept everything else — currency, interests, and country each re-priced/re-ranked live, and the checkout continued uninterrupted.

### Conversation 3 — shopping for someone else (gift buying)

Shows that the agent separates **who's shopping** (permanent profile) from **who
the item is for** (a transient "shopping for" context) — and that it filters and
ranks by the *recipient*, never the shopper.

Setup: the shopper is **Rahul** (male, 40, interests fitness + tech, India → ₹).
His own picks lead with fitness/tech gear (⌚ Smartwatch, 🧘 Yoga Mat…) — ⭐ = his interests.

**🧑 Shopper:** I'm looking for something for my kid

**🤖 Companion:** Of course! How old is your kid, and is it for a boy or a girl? 🎁

**🧑 Shopper:** he's 8

> 🔀 **`browse_for(recipient="my kid", gender="male", age="8")`** — filters by the
> **kid's** age (8 → kid), not Rahul's. His fitness interest is *not* applied.

**🤖 Companion:**
> Picks for **my kid** 🧒:
> 1. 🧱 **Building Blocks Set (200 pcs)** — ₹2,407
> 2. 🎤 **Karaoke Microphone Set** — ₹3,735
> 3. 🎲 **Board Game Night Bundle** — ₹4,897

**🧑 Shopper:** actually he's really into gaming

> 🔀 **`browse_for(... interests=["gaming"])`** — now ranks by the **kid's** interest.

**🤖 Companion:**
> Great — here's what a young gamer would love 🎮:
> 1. 🎮 **Handheld Gaming Console** — ₹16,517 ⭐ _gaming_
> 2. 🥽 **VR Headset (Immersive)** — ₹24,817 ⭐ _gaming_
> 3. 📚 **Graphic Novel Box Set** — ₹3,735
> 4. 🎤 **Karaoke Microphone Set** — ₹3,735

**🧑 Shopper:** show me things for me again

> 🔀 **`browse_for(recipient="myself")`** — clears the recipient; back to Rahul's own fitness/tech picks. (Confirming an order also resets to self.)

**Two points to make in the demo:**
- **Filter vs rank:** the recipient's **gender/age filters** the catalogue; the
  recipient's **interests rank** it (⭐). The shopper's own interests are never
  applied to a gift list — a dad's "fitness" shouldn't sort his kid's toys.
- **Nothing is overwritten:** "shopping for my kid" is transient (session only) —
  Rahul's permanent profile (male, 40, fitness/tech, ₹) is untouched throughout.
  This is the permanent-vs-temporary split in action.

**Demo reset:** type **"clear memory"** between runs to wipe both tiers and start from onboarding.

## Two memory tiers (one Memory Bank, split by scope)

| Tier | Holds | Lifespan |
|---|---|---|
| **Persona** (permanent) | profile: country, language, gender, age, interests, currency | never expires |
| **Task** (temporary) | in-progress order (step, product, address, payment) | **3-day TTL** (auto-destroyed) |

Keyed by `{app_name, user_id, tier}`; one keep-latest record per tier. Verified
genai-client Memory Bank API; mock fallback for local runs.

## Layout

```
gemini_context_intelligence/
  agent/
    store.py        two-tier Memory Bank backend (+ mock, TTL, keep-latest, clear)
    preferences.py  permanent profile + country→currency + age group
    order.py        temporary 5-step order state (3-day TTL)
    catalogue.py    mock products + preference-based recommend + currency pricing
    briefing.py     branching opener (onboarding / resume / step-1 recommendations)
    agent.py        ADK LlmAgent: flow tools + state injection + demo clear-memory
  tests/test_context.py   offline tests (8) — no LLM/network
  DESIGN.md, deploy_to_agent_engine.py, requirements.txt, env.dev.example
```

## Demo reset

Typing **"clear memory"** (or "reset demo") wipes **both** tiers and re-shows
onboarding — a presenter utility for demoing from scratch, not a product feature.

## Run offline

```bash
../../a2ui_gallary/.venv/Scripts/python tests/test_context.py
```

## Deploy (two-step, to enable real Memory Bank)

1. `cp env.dev.example .env.dev`, fill project/bucket. Deploy once with
   `AGENT_VAR_USE_MEMORY_BANK=false` → note the `reasoningEngines/<ID>`.
2. Set `AGENT_VAR_AGENT_ENGINE_ID` + `AGENT_VAR_USE_MEMORY_BANK=true`; redeploy.
3. Register the printed resource name in the GE Admin console (first deploy only).

```bash
../../a2ui_gallary/.venv/Scripts/python deploy_to_agent_engine.py
```

Do **not** set `GOOGLE_CLOUD_PROJECT`/`GOOGLE_CLOUD_LOCATION` as `AGENT_VAR_` — Agent
Engine reserves and injects them (the deploy script drops reserved names defensively).

### Starter prompts (turn-1 chips)

`starter_prompt.json` lists the clickable suggestions shown **before** the shopper
types (e.g. "Hi, let's start shopping"). Clicking one sends it as a normal message,
so it flows through the agent's opener. These are **GE app config, not agent code** —
apply them by filling `PROJECT_ID`/`ENGINE_ID` in `set_starter_prompts.sh` and running it:

```bash
PROJECT_ID=your-proj ENGINE_ID=your-ge-app-id bash set_starter_prompts.sh
```

## Later

- Localize the deterministic opener into the shopper's language.
- Richer catalogue / offers / stock; re-add A2UI (product cards + step buttons).
