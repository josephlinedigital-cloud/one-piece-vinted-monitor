# One Piece Vinted Deal Monitor

A lightweight **automatic UK bargain-alert bot** for One Piece trading cards. No URL pasting, no paid hosting, no web dashboard needed. Scheduled GitHub Actions scans newly listed items, checks a card-price baseline, checks recent **UK sold** comparisons, estimates costs/profit, and alerts via Telegram and/or email.

> **Important:** A live Vinted data feed is **not free forever** by default. This project uses Soldgraph's finite free trial credits, and halts when the configured budget is reached. It does not scrape Vinted directly. Running it requires your own data-service account, and permitted use is subject to that provider's terms and any relevant marketplace restrictions. The included demo uses entirely **fictional prices and fake links**.

## What works in this version

- Automated scheduled search of Vinted UK using Soldgraph's documented active listings endpoint.
- Rotating searches in `config.json` (one search per run, to conserve trial credits).
- Exact One Piece card code and stated variant recognition from the **listing title** (OP, EB, ST, PRB, P). No claim of photo-based or AI image recognition.
- Rejects ambiguous/unclear card variants, Japanese-labelled listings, graded cards, bundles and sealed products in **automatic confirmed-deal mode**.
- TickerMint benchmark with a current USD → GBP exchange rate, and **separate UK eBay sold comparables** through Soldgraph.
- Excludes known accepted-best-offer prices (which can mask the true sold amount), non-GBP sales, old sales, graded/japanese/mismatched variants.
- Requires **at least 3 recent valid UK sold comparisons** by default to qualify for an alert.
- Calculates Vinted purchase price including the provider-reported buyer fee plus *estimated* incoming shipping. Subtracts conservative resale haircut, selling fees, packaging and outgoing shipping.
- Telegram text alerts or SMTP email alerts with a Vinted link, value, ROI, confidence and verification warning.
- Saves seen listing IDs and avoids duplicate alerts. Can re-alert following major price drops.
- Stops before using more than 90 recorded Soldgraph trial credits. Tracks scans and sent alerts.
- Uses Python + GitHub Actions. No Supabase or Vercel account needed.

### Limitations

- **Live service not activated** until you supply `SOLDGRAPH_KEY`, `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (or SMTP secrets) in GitHub. Neither current listings nor real price API integration have been live-tested in this environment because outbound networking is unavailable.
- Search is **not instant**: default schedule runs every **4 hours**, and the four keywords rotate, so each keyword gets checked about once every **16 hours**. This intentionally conserves scarce free trial credits; it will miss short-lived bargains. GitHub scheduled jobs can also be delayed.
- The Soldgraph 100 initial credits are **one-off, not monthly free monitoring**. Default cap 90, leaving a buffer. A discovery request costs one credit; UK sold-price checks consume additional credits. At current frequency 90 could last a few weeks, depending on comparisons, but is not guaranteed.
- This version only confirms **well-identified individual cards**. Bundles, badly titled listings, image-only variants, proxies/fakes, and cards without sufficient matching UK sold records are skipped. This is deliberate to reduce false profit alerts.
- TickerMint offers **TCGplayer US market prices**, not UK sold prices. We do **not** label its USD market prices as UK actual sales. A product's language, card condition, rarity and authenticity can never be guaranteed from the seller title alone. **Always inspect photographs before buying.**
- UK eBay displayed sold prices for accepted offers are not necessarily the accepted sale price; such rows are excluded.
- Selling fees are user-configured conservative estimates, **not** a quote of your personal eBay fees or taxes. This is a hobby evaluation tool, not buying or reselling advice.
- Vinted terms restrict unauthorised automation; this repo deliberately does not bypass login restrictions, captcha, bot protection, or scrape the website.

## 1. See an offline demo (no account needed)

Install Python 3.12 or newer and run:

```bash
python -m pip install -r requirements.txt
python scanner.py --demo
python -m unittest discover -s tests -v
```

The demo prints a fictitious Shanks alert. It **does not** make any API calls or send notifications.

## 2. Create a private GitHub repo

1. Sign in to [github.com](https://github.com/) → New repository → name it `one-piece-vinted-monitor`.
2. Choose **Private**.
3. Upload all of the project files/folders **including `.github/workflows/scan.yml`**. Keep their paths identical. Commit them on the default branch (`main`).
4. Open the **Actions** tab and enable workflows if GitHub prompts you.

A private repo gives you GitHub-hosted periodic jobs within GitHub Free's minute allowance. The workflow commits `data/state.json` as the persistent deduplication and trial-credit state.

## 3. Get a listing-feed API key

1. Create an account at [Soldgraph](https://soldgraph.com/) and visit its API key page.
2. Read the current free trial and terms. In October 2026, free users have **100 lifetime requests**; verify the current offer before connecting.
3. Copy the API key privately. **Never paste it into `scanner.py`, `config.json`, a README, or chat.**
4. In your GitHub repo open **Settings → Secrets and variables → Actions → New repository secret**.
5. Name the secret `SOLDGRAPH_KEY` and paste the key into GitHub's secret field.

Docs: [Vinted listings](https://soldgraph.com/docs/api/vinted-listings), [UK eBay sold listings](https://soldgraph.com/docs/api/sold), [usage](https://soldgraph.com/docs/api/usage).

## 4. Set up FREE Telegram notifications

1. In Telegram, open the verified **@BotFather** account. Send `/newbot`; follow the steps, and copy your bot token privately.
2. Open your newly-created bot's chat and press **Start**. Send a message such as `hello`.
3. To get your chat ID **without putting your bot token in a web browser address**, on your own computer run:

   macOS/Linux:
   ```bash
   export TELEGRAM_BOT_TOKEN='YOUR_BOT_TOKEN'
   python telegram_setup.py
   ```

   Windows PowerShell:
   ```powershell
   $env:TELEGRAM_BOT_TOKEN='YOUR_BOT_TOKEN'
   python telegram_setup.py
   ```

4. In GitHub repo Actions secrets, add:
   - `TELEGRAM_BOT_TOKEN` = the BotFather token
   - `TELEGRAM_CHAT_ID` = the chat ID printed by `telegram_setup.py`

Telegram Bot API docs: https://core.telegram.org/bots/api

### Prefer email?

Email is optional and can be used alongside Telegram. Set the following **repository secrets** using a trusted SMTP mailbox:

| Secret | Example/value |
| --- | --- |
| `SMTP_HOST` | `smtp.gmail.com` (if supported by your account) |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | Your SMTP login |
| `SMTP_PASSWORD` | Your SMTP app password (not your normal account password) |
| `ALERT_EMAIL_FROM` | Your email address |
| `ALERT_EMAIL_TO` | The address where alerts should arrive |

If using Google, an app password may require 2-Step Verification and availability depends on your account. Avoid committing any credentials.

## 5. Start the automation

1. Go to **Actions → One Piece Vinted Monitor → Run workflow** and set `test_alert = true`. This **does not use any listing credits** and sends a test message to Telegram/email.
2. Run the workflow again with `test_alert = false` for a genuine scan.
3. Open the resulting job log. It should say what query it checked and whether it found matches.
4. If it finds a sufficiently well-confirmed bargain, it will send a Telegram/email alert.
5. The workflow runs again on the schedule in `.github/workflows/scan.yml`.
6. View `data/state.json` to see total scans, messages sent, errors and credits used.

### Change what counts as a bargain

Edit `config.json`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `min_profit_gbp` | 15 | Estimated profit after buying and selling costs |
| `min_roi_percent` | 30 | Minimum return on money spent |
| `min_score` | 80 | Minimum confidence/margin ranking |
| `max_listing_price_gbp` | 200 | Maximum asking price |
| `min_uk_sold_comps` | 3 | Recent matching UK sold records required |
| `max_sold_checks_per_run` | 1 | Maximum extra paid API lookups per run |
| `max_total_api_credits` | 90 | Absolute local safety stop on trial usage |
| `shipping_in_gbp` | 3.49 | Estimated Vinted delivery cost; update for your shipping choice |
| `selling_fee_percent` | 13 | Conservative selling fee estimate; adjust for your seller status |
| `resale_haircut_percent` | 10 | Safety discount to UK sold median |
| `search_terms` | 4 One Piece searches | One is automatically checked per scheduled run |

To make it faster, edit `.github/workflows/scan.yml` cron schedule; for example `17 */2 * * *` means every two hours. **More frequent scanning spends finite free API credits much faster.** GitHub's minimum scheduled frequency is five minutes, but starts can be delayed.

## 6. Trial credits and ongoing operation

The automatic scanner deliberately **stops when the configured trial budget is exhausted**, rather than silently incur recurring costs. It can resume if you obtain a permitted ongoing feed and raise/reset your own configured budget to match the provider's purchased quota. Do not change the budget just to bypass your provider's limits. Alternatives include a licensed provider with a genuinely recurring free allowance, if one becomes available, or a source that gives permission to use its feed.

## How the score works

Only listings with an exact card number and described variant qualify. The program first uses a USD TickerMint baseline to avoid spending Soldgraph credits on obviously poor margins. It then obtains recent UK sold listings, removes graded/mismatched/accepted-offer comparables, takes their median, applies a conservative resale discount, and calculates:

```
Total purchase = displayed Vinted total (with buyer fee) + estimated incoming postage
Expected resale = median UK sold price × (1 - resale haircut %)
Selling costs = expected resale × fee % + outgoing postage + packaging
Expected profit = expected resale - total purchase - selling costs
ROI % = expected profit / total purchase × 100
```

An 80+ score requires strong estimated margin, multiple comparable sales and few quality flags. **A score isn't a guarantee of actual market value or authenticity.**

## Data sources & credits

- [TickerMint developer API](https://tickermint.cards/developers/one-piece) — free for **personal/non-commercial use** subject to its terms; TCGplayer USD benchmark, not verified UK sales. Each alert links back to TickerMint.
- [Soldgraph Vinted active listing API](https://soldgraph.com/docs/api/vinted-listings) — UK listings via licensed/third-party service with finite trial credits.
- [Soldgraph eBay UK sold listings API](https://soldgraph.com/docs/api/sold) — available sold-listing evidence subject to source limitations.
- [Frankfurter API](https://frankfurter.dev/) — live USD→GBP indicative exchange rate.
- [Vinted terms](https://www.vinted.co.uk/terms_and_conditions) — check current terms and permitted automated use.

No auto-purchasing, browser-login scraping, circumvention tools or personal-account automation are included.
