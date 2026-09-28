# Alerts and email: how they work, a rating, and the plan

| | |
|---|---|
| Document | 14 |
| For | The HOD, whoever maintains Cirqen, and IT at the site |
| Covers | Stock alerts, accessory requests, weekly reports, work orders, and email while offline |
| Revised | 28 September 2026, after the hospital's review: **no email per work order**; the weekly report is how the HOD hears about work orders; the low-stock limit is set on the stock dashboard |
| Checked against | `cirqen-labs` branch `ansur-v2` (28 September 2026) and the installed app's `config.json` |
| **Rating** | **4.2 / 10 today. About 8.3 / 10 after the six phases in §5 (0–5).** |

## 0. The answer on one page

**What you asked for, and where it stands:**

| You want | Today |
|---|---|
| Stock alert emailed to the HOD, copying one person in the workshop | **Not like this.** One in-app note plus a direct email goes to the workshop's Engineer In-charge each morning. **The HOD is not told.** Every PC runs the job, so the same alert is sent once per PC |
| Email to the HOD when an accessory is requested | **Nothing is sent** |
| Email to the requester when the HOD approves or declines | **Nothing is sent** |
| Email to the HOD when the accessory is received | **Nothing is sent** |
| Queued while offline and sent when the internet returns | **Partly.** Work-order email is queued, but gives up after about 4 hours offline. Stock alerts are not queued and are lost |
| A lower limit per part on the stock dashboard; at or below it the part shows as **running low** | **No.** The dashboard colours stock by fixed numbers (red below 5, yellow below 10) whatever the part. Each part's reorder level can only be set on a separate page (*Resources → Stock alerts*) |
| HOD emailed when the Engineer In-charge submits the weekly report | **Nothing is sent.** Weekly reports exist (Report Hub) but submitting one tells nobody |
| Work orders: **no email per work order** (10–15 a day at a level 6 hospital is noise) | **Two emails are sent per work order today** (submitted → in-charge, copied to the HOD; decided → technician, copied to the HOD). They should stop; the bell notification stays |

**And one blocker before any of it:** outgoing email is not configured on the installed app (`email.host_user` is empty in its `config.json`). Until it is, no email of any kind leaves the PC.

**The good news:** the hard part already exists. `notifications` has a proper outbox: messages are queued in the local database, sent every minute, retried with growing waits, never sent twice, and follow the hospital's copy rule (mail to staff copies the HOD; mail to the HOD copies the Deputy HOD). The plan builds on that. It routes everything through it, stops it giving up while offline, and adds the missing events.

## 1. How it works today

### 1.1 The two alert systems

```
 EVENT ON THIS PC                         SYSTEM A: notifications (good)
 work order saved ----signal---->  queue()  -> EmailOutbox row (local DB)
                                     |          + bell notification (synced)
                                     |
                         flush_outbox every minute: send, or retry in
                         2, 4, 8 ... min; after 8 failures -> "failed"

 DAILY JOB ON EVERY PC                    SYSTEM B: core.notify (weak)
 assets.daily_alerts 06:47 ------> notify() -> bell notification (synced)
   standards due, contracts ending,           + send_mail() straight away
   LOW STOCK                                    (no queue: offline = lost)
 monthly_hod_report on the 1st ---> EmailMessage.send() straight away
```

| | System A: `notifications` | System B: `core.notify` |
|---|---|---|
| Used by | Work orders, daily digest | Stock, standards, contracts, monthly report |
| Queued if offline | Yes, up to 8 attempts (≈4 h) | **No: the email is lost** |
| Copy rule (HOD / Deputy) | Yes | No |
| Sent once per event | Yes (dedupe key) | **No: every PC sends its own copy** |
| Runs on | The PC where the event happened | Every PC with the background worker |

### 1.2 Stock

- **Stock goes down** only when a work order is approved (`jobcard.deduct_stock`). It goes back up if an approved work order is later declined, or when a received accessory request is accepted.
- **The reorder level** is set per part on *Resources → Stock alerts*. The page lists parts at or below it.
- **The stock dashboard** (*Parts & tools*), where stock is actually managed, ignores that level: it colours the count red below 5 and yellow below 10 for every part, whether a part is used ten a day or one a year.
- **The alert** is the 06:47 daily job: for each workshop, one line listing its low parts, sent to the Engineer In-charge (or every technologist if there is none). It does not reach the HOD. It repeats every morning while the part stays low, and it is sent from every PC.
- **Two accuracy problems** make any stock alert less trustworthy:
  - The deduction is a read-then-write on the local copy. Two PCs approving work orders that use the same part both start from the same count.
  - Spare parts sync with **last-write-wins**, so one of those two deductions is lost.

### 1.3 Accessory requests

The flow is complete; it just tells nobody:

1. A technologist requests a new accessory or a restock (**Pending**).
2. The HOD approves (setting quantity and unit cost) or declines, with a reason.
3. Someone in the workshop marks it **received** (**Accepted**), which creates the accessory or adds to its stock.

Each step is recorded in the request's history. There is no email and no bell notification at any step; people find out by opening the page.

### 1.4 Work orders

| Event | Email to | Copied | Queued |
|---|---|---|---|
| Submitted (Waiting Approval) | The department's in-charge (NIC) | HOD (copy rule) | Yes |
| Approved or declined | The technician who did the work | HOD (copy rule) | Yes |

Also in the daily digest, **but only if one PC has `notifications.digest_sender` switched on**, which the installed app does not:

- In-charges: work orders waiting for them.
- Technicians: their work orders declined this week.
- The HOD: per-workshop counts.

**The hospital's decision:** this is too much mail. At a level 6 hospital a technician does 10–15 work orders a day, and with the copy rule the HOD would be copied on all of them. Per-work-order email stops. People still see work orders in the app (the bell and the approval queues), and the HOD hears about them through the **weekly report**.

### 1.4a Weekly reports

The Engineer In-charge submits a weekly (or monthly, quarterly, annual) report for the workshop in Report Hub. A report records the workshop, the period and remarks; its figures (work orders, parts used, costs) are built from the records when it is opened or exported. **Submitting one sends no email and no bell notification**, so the HOD finds out only by looking.

### 1.5 Offline

The app keeps working offline: requests, approvals and receipts are saved locally and sync later.

- **System A queues email.** But being offline counts as a failed attempt. After 8 attempts at 0, 2, 6, 14, 30, 62, 126 and 254 minutes the message is marked **failed** and never sent. **A PC offline for more than about 4 hours loses its queued email.**
- **System B does not queue at all.**
- **Either way**, mail queued on a PC waits for that PC to be switched on again.

## 2. Rating: 4.2 / 10

| Dimension | Weight | Score | Why |
|---|---|---|---|
| Foundation: outbox, retries, dedupe, copy rule | 15% | **8.0** | Well built, tested, one queue per PC so 500 PCs do not repeat event mail |
| Covers the events the hospital needs | 25% | **3.0** | Stock goes to the wrong person; accessory requests and weekly reports send nothing; work orders send too much |
| Survives being offline | 20% | **4.0** | Work-order mail queued but abandoned after ≈4 h; stock and monthly report mail lost |
| Sent once, not once per PC | 10% | **3.0** | Daily alerts and the monthly report run on every PC |
| Can be set up and checked | 15% | **3.0** | Email unset on the installed app; no test button; no page showing what is queued or failed |
| Stock figures behind the alerts | 15% | **4.5** | Read-then-write deduction; last-write-wins between PCs loses deductions |
| **Overall** | | **4.2 / 10** | |

**Work orders on their own: 5.0 / 10.** The mail is reliable and reaches the right people, but it is the wrong amount: one email per work order, with the HOD copied, is noise at a busy hospital, while the weekly report the HOD actually reads sends nothing. Phase 4 swaps the two.

## 3. The design

### 3.1 One way to send

Every email goes through the outbox (`notifications.mailer.queue`). `core.notify` keeps making bell notifications but queues its email there instead of sending directly. The monthly report is queued too, which needs the outbox to carry an attachment.

### 3.2 Offline without losing mail

- **An attempt that fails because the PC cannot reach the mail server** (no internet, DNS failure, connection refused, timeout) no longer counts against the 8-attempt limit. The message waits and is retried every few minutes (at most every 30) until the internet returns.
- **Only a real refusal counts as a failure**, e.g. the server rejects the address or the password. After 8 of those the message is marked failed and shown on the outbox page.
- **Anything still unsent after 7 days** is marked *expired* rather than sent late (a week-old "request approved" is noise). The number of days is a setting.
- **The mail stays on the PC where the event happened**, because that PC made it. If that PC is switched off, its mail waits. The bell notification, which syncs, still reaches the person on any PC.

### 3.3 Who gets what

The copy rule stays exactly as the hospital asked: mail to staff copies the HOD; mail to the HOD copies the Deputy HOD. Two additions:

- **The person who did the action is never emailed about it.** The HOD who approves a request is not copied on "your request was approved".
- **Each workshop gets a stock contact**: the one person copied on stock alerts. It is set on the workshop page and defaults to the Engineer In-charge.

| Event | To | Copied | When |
|---|---|---|---|
| **Stock falls to or below its lower limit** | HOD | The workshop's stock contact (+ Deputy HOD, copy rule) | At the moment it crosses, once per episode |
| Still low | HOD | — | In the HOD's daily digest, while it lasts |
| **Accessory requested** | HOD | Deputy HOD | At once |
| **Request approved or declined** | The requester | HOD (copy rule), not the HOD who decided | At once; states quantity, unit cost, total and reason |
| **Accessory received** | HOD | Deputy HOD | At once; states who received it, quantity and the new stock level |
| **Weekly report submitted** (by the Engineer In-charge) | HOD | Deputy HOD | At once; a summary in the email and the report PDF attached |
| Weekly report not submitted by Tuesday | The Engineer In-charge | — | Once, from the site's sender PC |
| Work order submitted, approved or declined | **No email.** Bell notification to the same people as today | — | — |

**Why so few:** email is for things a person must act on, or that the HOD must know without looking. Everything else stays in the app, where the bell, the approval queues and the digest already show it.

### 3.4 The lower limit and "running low"

- **Set on the stock dashboard.** The *Lower limit* is a field on the add and edit forms for each part, and can be changed inline in the stock table (HOD and Engineer In-charge). It is the same field as today's reorder level (`reorder_level`), so the Stock alerts page and the dashboard show the same number.
- **Three states, one rule for every screen:**

| Stock | Shown as |
|---|---|
| 0 | **Out of stock** (red) |
| At or below the lower limit | **Running low** (amber) |
| Above the lower limit, or no limit set | **In stock** (green) |

- **A "Running low" filter** on the dashboard lists every part that needs ordering, with its supplier and a **Request restock** button.
- The fixed "red below 5, yellow below 10" colouring goes.

### 3.5 "Once per episode" for stock

A part's stock can go up and down many times a day on several PCs. The alert fires when it **crosses** from above the reorder level to at or below it, and not again until it has gone back above. The crossing is recorded on the part (`low_stock_alerted_at`, synced), so other PCs see that the alert was sent. Restocking above the lower limit clears it.

### 3.6 Scheduled jobs run once per site

The daily alerts, the weekly-report reminder and the monthly report run only on the PC marked as the site's sender (`notifications.digest_sender`, already used by the digest). The HOD sets it on the notifications settings page instead of editing `config.json`. If no PC is marked, the settings page warns.

## 4. Stock you can trust

An alert is only as good as the count behind it.

1. **Atomic deduction on the PC.** The count is reduced in a single database update (`F('stock_count') - n`), so two approvals on the same PC cannot both start from the same figure.
2. **A stock movements table between PCs.** Each receipt, use and correction becomes a row (part, quantity + or −, why, work order or request, who, when). Rows are only ever added, so syncing loses nothing: two PCs' movements both arrive. The stock count is the total of the rows, kept up to date on each PC. It also gives the HOD a history of every movement of every part.
3. **Reserved stock.** Parts on work orders still *Waiting Approval* show as reserved on the stock page, so a technician does not plan with parts already promised.

## 5. The plan, in phases

Each phase is shippable on its own. Estimates are working days for one developer, including tests.

### Phase 0: make email work and visible (2–3 days)

- **Email settings page** (HOD, *Administration*): server, port, sender, password, and **Send a test email**. The password is stored the way the sync token is, never shown back.
- **Outbox page**: what is waiting, sent and failed, with the error for each, **Retry** and **Discard** buttons, and the last successful send.
- **Offline no longer counts** against the 8-attempt limit; unsent mail expires after 7 days (§3.2).
- **Choose the site's sender PC** on the settings page (§3.6), with a warning when none is chosen.
- *Done when:* a test email arrives; mail written with the network unplugged for a day goes out when it is plugged back in; the outbox page shows it.

### Phase 1: accessory requests (2 days)

- **Three emails and bell notifications**: requested → HOD; decided → requester; received → HOD (§3.3). Each links straight to the request.
- **Sent from the view where the action happens.** Rows arriving from other PCs through sync never send, so each event is mailed exactly once.
- **Keyed** `accessory-request:{id}:{status}`, so a double-click cannot send twice.
- *Done when:* a request made offline, approved, and received offline produces three emails once the PCs are online, and no duplicates.

### Phase 2: running low, and stock alerts to the HOD (3 days)

- **The lower limit on the stock dashboard** (§3.4): a field on the add and edit forms and inline in the table; *Out of stock / Running low / In stock* badges; a *Running low* filter; the fixed 5/10 colouring removed.
- **A stock contact per workshop** (§3.3), defaulting to the Engineer In-charge. It syncs, which needs one HQ migration.
- **The crossing alert** (§3.5), sent from wherever stock changes: work order approved, declined after approval, received, or edited by hand. It says which part, the count, the reorder level, the supplier and the suggested order quantity, and links to **Raise a restock request** with the form already filled in.
- **A low-stock section in the HOD's digest**; `daily_alerts` stops mailing stock.
- *Done when:* a part's lower limit is set on the dashboard and it shows *Running low* at that count; approving a work order that takes a part to its lower limit sends one email to the HOD with the stock contact copied; a second approval does not; a restock resets it.

### Phase 3: stock you can trust (3–4 days)

- Atomic deduction, the stock movements table, and reserved stock (§4).
- A one-off migration writes an opening movement per part, equal to today's count, so nothing changes on the day it ships.
- *Done when:* two PCs each approving a work order that uses the same part, while offline, end with both deductions counted after sync.

### Phase 4: weekly reports by email; work orders by bell only (2 days)

- **Work-order email switched off.** Submitted, approved and declined keep their bell notifications, but queue no email. A setting (*Email about each work order*, off) keeps the option for a smaller site.
- **Weekly report submitted → the HOD** (copied to the Deputy HOD), sent from the PC where the Engineer In-charge submits it. The email carries a short summary: work orders done by type, still waiting approval, declined, parts used and their cost, parts running low, devices down. The report PDF is attached. Keyed `report:{id}`, so resubmitting does not send it twice.
- **A reminder** to the Engineer In-charge if last week's report is not in by Tuesday, from the site's sender PC.
- **The monthly report** to the HOD moves to the outbox too, with its PDF, and is sent from the sender PC only (today every PC sends it).
- *Done when:* a technician completing 15 work orders in a day sends no email; the Engineer In-charge submitting the weekly report sends the HOD one email with the PDF, even if it was submitted offline.

### Phase 5: preferences and history (2 days)

- **Per-person preferences**: each user can switch off the emails they do not need (bell notifications stay). The HOD can make some compulsory.
- **"Emails about this"** on each work order and accessory request: who was told what, and when it was delivered.
- **Delivery counts on the HQ dashboard**, so a site whose mail has stopped is noticed.

### Order and totals

| Phase | Days | Rating after |
|---|---|---|
| 0: email works and is visible | 2–3 | 5.9 |
| 1: accessory requests | 2 | 6.4 |
| 2: running low, and stock alerts to the HOD | 3 | 7.0 |
| 3: stock you can trust | 3–4 | 7.5 |
| 4: weekly reports by email; work orders by bell only | 2 | 8.0 |
| 5: preferences and history | 2 | **8.3** |
| **Total** | **14–17** | |

Phases 0–2 deliver the stock and accessory emails and the running-low status in about a week. Phase 4 adds the weekly report email and switches work-order email off.

## 6. Decisions

**Settled by the hospital (28 September 2026):**

- No email per work order; the bell notification stays.
- The HOD hears about work orders through the weekly report, emailed when the Engineer In-charge submits it.
- Each part's lower limit is set on the stock dashboard; at or below it the part is *running low*.

**Still open:**

1. **Stock contact:** one person per workshop (recommended, set on the workshop page), or always the Engineer In-charge?
2. **Weekly report email:** attach the PDF (recommended), or send a link only?
3. **Late weekly report:** remind the Engineer In-charge on Tuesday? Tell the HOD if it is still missing on Thursday?
4. **Unsent mail expires after:** 7 days suggested.
5. **Who may change a lower limit:** the HOD and Engineer In-charge (recommended), or every technologist?

## 7. Progress (28 September 2026)

Built on branch `alerts-email` (cirqen-labs) with HQ migrations on `ansur-calibration-columns` (hq_server). Not committed.

| Phase | State | What was built |
|---|---|---|
| 0 | **Done** | *Settings → Email* (HOD): mail server, **Send test email**, "this PC sends the site's scheduled email". Outbox page with Retry and Discard. Offline no longer uses up attempts; unsent mail expires after 7 days. The older direct-send alerts and the monthly report go through the outbox, from the sender PC only. |
| 1 | **Done** | Request → HOD; approved or declined → requester (not the HOD who decided); received → HOD with the new stock. Once each, offline-safe. |
| 2 | **Done** | Lower limit set in the stock table by the HOD or the workshop's Engineer In-charge. *Out of stock / Running low / In stock* badges and filters. Running low emails **everyone in the workshop** once per shortage (HOD copied), mentioning the automatic restock request. "Parts running low" in the HOD's digest. |
| 3 | **Done** | Stock ledger (`StockMovement`): every change is a row that syncs; each PC sets a part's count to the total of its movements every minute. Two PCs using the same part while apart both count. Opening balances are written by the site sender PC. The HOD's stock edit is recorded as a stock take (adjustment). |
| 4 | **Done** | No email per work order (bell only; a setting turns them back on). A submitted Report Hub report reaches the HOD **as a PDF**. On Monday from 08:00 the Engineer In-charge is reminded if last week's report is missing. |
| 5 | **Done** | *My email* page for everyone: switch off stock, accessory, report, digest, alert or work-order email (the bell still comes; the weekly-report reminder cannot be switched off). The HOD is still copied when every main recipient has switched an email off. The choice syncs. The outbox can be searched by part, workshop, person or address. Each PC reports its outbox on the HQ heartbeat (waiting, oldest wait, refused or expired this week, last sent); HQ's `/api/sync/ops` lists sites whose email has waited over 24 hours (`EMAIL_STUCK_HOURS`) or was refused or expired. |

**Before it goes live:**

1. Deploy HQ with its new migrations (`2026_add_ansur_calibration.sql`, `2026_add_equipment_qr_labels.sql`, `2026_add_accessory_low_stock.sql`, `2026_add_stock_movements.sql`, `2026_add_user_email_preferences.sql`); HQ applies them on boot.
2. Add `public.parts_tools_stockmovement` to HQ's `TABLES` environment variable on Render, after `public.parts_tools_accessoryrequest`. Without it the movements do not sync and each PC keeps its own ledger.
3. On one PC per site, fill in *Settings → Email*, send a test email, and tick "This PC sends the site's scheduled email". That PC also writes the stock opening balances.
