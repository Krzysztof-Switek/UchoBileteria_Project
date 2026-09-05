# Ticketing System Specification

## Project

**Project name:** Ticketing system for a music club  
**Document:** `TICKETING_SPEC.md`  
**Version:** MVP draft v1  
**Status:** working specification  

---

## 1. Purpose

The goal is to build a complete ticketing system for a music club.

The system should support:

- creating and publishing events,
- selling tickets online,
- handling payment confirmation,
- issuing tickets with QR codes,
- checking tickets at the entrance,
- handling refunds,
- publishing events to the club website,
- adding events to Google Calendar,
- sending ticket emails,
- exporting emergency entrance lists,
- providing admin reports and audit logs.

The system must be fast, reliable, simple to operate, and as automated as possible.

---

## 2. MVP scope

The first production-ready version should support only the features required for safe ticket sales and entrance control.

MVP includes:

- one public event page per event,
- one room / one venue space,
- one event date,
- no numbered seats,
- global event capacity limit,
- multiple price pools for one standard ticket type,
- automatic pool activation,
- online purchase flow,
- QR ticket generation,
- email delivery of tickets,
- one entrance gate / one check-in point,
- online QR scanning,
- emergency offline list,
- basic admin panel,
- manual admin refund flow,
- Google Calendar integration,
- audit logs,
- test environment before production.

---

## 3. Out of scope for MVP

The MVP does **not** support:

- numbered seats,
- multiple rooms,
- multiple stages,
- multiple dates for one event,
- private events,
- hidden events,
- invite-only events,
- events available only through a private link,
- discounted tickets,
- student tickets,
- free tickets,
- guest lists,
- promo codes,
- vouchers,
- season passes,
- ticket resale,
- multiple entrance gates,
- complex offline multi-device synchronization.

These features may be added later after the MVP is stable.

---

## 4. Core business assumptions

### 4.1 Event model

One event means:

- one public music event,
- one room,
- one date,
- one entrance gate,
- one global capacity limit.

The event is public and visible on the website once published.

### 4.2 Ticket model

The system supports only one ticket type:

- standard admission ticket.

The price of the ticket is not defined by ticket type.  
The price is defined by the currently active ticket pool.

### 4.3 Entrance model

The MVP assumes:

- one physical gate,
- one check-in point,
- one staff workflow for scanning or manually checking tickets.

Because there is only one entrance gate, offline emergency check-in can be handled with one exported list. There is no need for complex synchronization between multiple gates in the MVP.

---

## 5. Event model

### 5.1 Event rules

Each event has:

- title,
- slug,
- description,
- venue name,
- venue address,
- start date and time,
- end date and time,
- sales start date,
- sales end date,
- global capacity limit,
- current sold count,
- status,
- optional Google Calendar event ID.

### 5.2 Event status

Allowed event statuses:

```text
DRAFT
PUBLISHED
SOLD_OUT
SALES_CLOSED
CANCELLED
FINISHED
```

### 5.3 Event capacity

The event has one global capacity limit.

The system must never sell more tickets than `capacityTotal`.

Example:

```text
capacityTotal = 300
soldTotal <= 300
```

Sales close automatically when:

- event capacity is reached,
- the active pool is sold out and no next pool is available,
- the sales end date has passed,
- the event is manually closed or cancelled by an admin.

---

## 6. Ticket pools

### 6.1 General rule

An event may have multiple ticket pools.

Each pool represents a price tier, for example:

```text
Early Bird
Regular
Last Call
```

Each pool has:

- name,
- priority,
- price,
- capacity,
- sold count,
- sales start date,
- sales end date,
- manual admin status,
- computed status.

### 6.2 Pool activation rule

The MVP uses activation option C:

```text
A ticket pool becomes sellable when:
- its start date has been reached
OR
- the previous pool has sold out.
```

Additionally, an admin can manually:

- open a pool,
- close a pool,
- stop a pool,
- force transition to another pool.

### 6.3 One active sellable pool

For MVP, the system should sell from only one active pool at a time for a given event.

If multiple pools match the activation conditions, the system chooses the pool with the lowest priority number that is still sellable.

Example:

```text
Pool 1: Early Bird, priority 1, 50 tickets, 40 PLN
Pool 2: Regular, priority 2, 150 tickets, 60 PLN
Pool 3: Last Call, priority 3, 100 tickets, 80 PLN
```

If Early Bird sells out before the Regular start date, Regular may become active immediately.

### 6.4 Pool status

Allowed pool statuses:

```text
DRAFT
ACTIVE
SOLD_OUT
CLOSED
```

### 6.5 Manual pool status

Allowed manual statuses:

```text
AUTO
FORCED_OPEN
FORCED_CLOSED
```

`AUTO` means the pool follows the automatic activation rules.  
`FORCED_OPEN` means the admin manually opened the pool.  
`FORCED_CLOSED` means the admin manually closed the pool.

---

## 7. Buyer data

### 7.1 Required buyer data

The ticket is not named.

The buyer provides only:

```text
email
```

No name is required in MVP.

### 7.2 Ticket ownership

The ticket belongs to the buyer email for operational purposes, but entrance verification is based on:

- QR code,
- ticket status,
- emergency ticket code if QR or internet fails.

The staff does not need to verify identity documents at the entrance in MVP.

---

## 8. Ticket identification

Each issued ticket must have:

- internal ticket ID,
- public short ticket code,
- secure QR token,
- hashed QR token stored in the database,
- buyer email,
- ticket status,
- event ID,
- order ID,
- pool ID,
- creation timestamp,
- optional check-in timestamp.

### 8.1 QR code

The QR code should contain a secure random token or verification URL.

Recommended format:

```text
https://example.com/ticket/verify?t=<secure_random_token>
```

The database should store only the hash of the QR token, not the raw token.

### 8.2 Short ticket code

Each ticket should display a short human-readable code, for example:

```text
A7K9-42
```

This code is used for manual lookup when QR scanning fails.

### 8.3 Ticket email

The buyer receives an email containing:

- event title,
- event date,
- venue,
- buyer email,
- QR code,
- short ticket code,
- basic entrance instructions.

---

## 9. Ticket status

Allowed ticket statuses:

```text
ISSUED
CHECKED_IN
REFUNDED
CANCELLED
INVALIDATED
```

### 9.1 Status rules

A ticket can be checked in only if its status is `ISSUED`.

A ticket cannot be checked in if its status is:

- `CHECKED_IN`,
- `REFUNDED`,
- `CANCELLED`,
- `INVALIDATED`.

A ticket that has been checked in cannot be used again.

---

## 10. Order model

An order represents a purchase attempt.

Each order has:

- order ID,
- event ID,
- buyer email,
- selected pool ID,
- quantity,
- total amount,
- currency,
- payment provider,
- payment session ID,
- payment status,
- order status,
- created timestamp,
- paid timestamp,
- refunded timestamp if applicable.

### 10.1 Order status

Allowed order statuses:

```text
CREATED
PAYMENT_PENDING
PAID
FAILED
EXPIRED
REFUNDED
CANCELLED
```

### 10.2 Ticket quantity per order

Open decision.

Recommended MVP rule:

```text
1 to 10 tickets per order.
```

Each ticket in the order must have its own QR code.

---

## 11. Purchase flow

### 11.1 Normal flow

1. User opens the event page.
2. System displays current active ticket price.
3. User enters email.
4. User selects ticket quantity.
5. System creates an order.
6. System starts payment session with payment provider.
7. User pays.
8. Payment provider sends webhook to backend.
9. Backend verifies webhook signature.
10. Backend marks order as paid.
11. Backend issues tickets.
12. Backend sends ticket email.
13. Event sold count and pool sold count are updated.

### 11.2 Critical rule

Tickets are issued only after confirmed payment webhook.

The system must not issue final tickets based only on frontend redirect after payment.

---

## 12. Capacity and transaction rules

When creating an order or reserving capacity, the backend must check:

- event status is `PUBLISHED`,
- event sales window is active,
- event is not sold out,
- active pool exists,
- active pool is not sold out,
- requested quantity is available,
- event capacity is not exceeded,
- pool capacity is not exceeded.

The capacity update must be atomic.

The system must protect against overselling when many users buy tickets at the same time.

---

## 13. Payment provider integration

**Decision (2026-06-11):** target provider is **Stripe**. Before Stripe goes live, the system runs in **demo payment mode** (see section 31).

All payment logic is hidden behind a `PaymentProvider` interface:

```text
create_checkout_session(order)
handle_webhook(request)
refund(order | tickets)
```

Implementations: `DemoProvider` (first), `StripeProvider` (second). The rest of the system never talks to a provider directly.

The system should use payment provider webhooks as the source of truth.

### 13.1 Payment webhook requirements

The backend must:

- verify webhook signature,
- store provider event ID,
- prevent double processing of the same webhook,
- map provider payment event to internal order,
- issue tickets only once,
- handle late or repeated events safely.

### 13.2 Payment event log

The system should store payment events in a dedicated collection/table.

Each payment event should include:

- provider name,
- provider event ID,
- event type,
- related order ID,
- processed timestamp,
- processing status,
- raw payload hash or safe payload subset.

---

## 14. Refunds

Refund policy is not fully specified yet.

The system should support admin-triggered refunds in MVP.

### 14.1 Refund flow

1. Admin opens order.
2. Admin selects refund action.
3. Backend validates that refund is possible.
4. Backend calls payment provider refund API.
5. Payment provider confirms refund through webhook or API response.
6. Backend marks order or selected tickets as refunded.
7. Refunded tickets become invalid.
8. Refunded tickets cannot be checked in.
9. Buyer receives refund confirmation email.

### 14.2 Open refund decisions

To decide later:

- full refunds only or partial refunds,
- refund deadline,
- refund fee handling,
- what happens after event cancellation,
- what happens after event postponement,
- whether buyer can request refund automatically,
- whether refund always requires admin approval.

---

## 15. Entrance control

### 15.1 Normal online check-in

1. Staff opens check-in panel.
2. Staff selects event.
3. Staff scans ticket QR code.
4. Backend verifies ticket token.
5. Backend checks ticket status.
6. If valid, ticket is marked as `CHECKED_IN`.
7. UI displays success.
8. If invalid, UI displays clear rejection reason.

### 15.2 Check-in results

Possible scan results:

```text
VALID_CHECKED_IN
ALREADY_CHECKED_IN
REFUNDED
CANCELLED
INVALID_TOKEN
WRONG_EVENT
SERVER_ERROR
```

### 15.3 One gate assumption

The MVP assumes one entrance gate and one check-in point.

This means the fallback offline process can be simple and controlled.

---

## 16. Emergency offline entrance procedure

Before each event, admin exports an emergency check-in list.

The list should contain:

- event title,
- event date,
- short ticket code,
- buyer email,
- ticket status,
- optional order ID,
- empty field for manual check-in mark.

### 16.1 Offline use case

The emergency list is used when:

- internet fails,
- QR scanner fails,
- ticket email is visible but QR cannot be scanned,
- backend is temporarily unavailable.

### 16.2 Offline manual verification

Staff searches the list by:

- short ticket code,
- buyer email.

If ticket is valid, staff manually marks it as used on the printed or exported list.

After the event, admin may manually update check-in status in the system if needed.

### 16.3 MVP decision

Offline multi-device synchronization is not required in MVP because there is only one gate.

---

## 17. Admin panel

The admin panel should support:

- creating events,
- editing draft events,
- publishing events,
- cancelling events,
- closing sales,
- creating ticket pools,
- opening and closing pools manually,
- viewing orders,
- viewing tickets,
- viewing sales totals,
- exporting emergency check-in lists,
- triggering refunds,
- viewing audit logs.

### 17.1 Admin event creation flow

When an admin creates and publishes an event, the system should:

1. save event in database,
2. create public website page or public event record,
3. create Google Calendar event,
4. prepare ticket pools,
5. make event available for ticket sales when sales start date is reached.

---

## 18. Google Calendar integration

When an event is published, the system should create or update the corresponding Google Calendar event.

The event should include:

- title,
- date and time,
- location,
- short description,
- link to public event page.

The internal event should store:

```text
calendarEventId
```

If event details change, the calendar event should be updated.

If the event is cancelled, the calendar event should be updated or cancelled according to the chosen policy.

---

## 19. Website integration

Published events should automatically appear on the public website.

The public event page should display:

- title,
- date and time,
- venue,
- description,
- current ticket price,
- sales status,
- buy ticket button,
- sold out message if applicable,
- sales closed message if applicable.

The public page should not expose internal IDs or sensitive information.

---

## 20. Email integration

The system should send emails for:

- ticket purchase confirmation,
- ticket delivery,
- refund confirmation,
- event cancellation if needed,
- event change if needed.

Ticket email must include:

- QR code,
- short ticket code,
- buyer email,
- event details,
- entrance instructions.

---

## 21. Roles and permissions

Recommended MVP roles:

```text
ADMIN
EVENT_MANAGER
SALES_MANAGER
DOOR_STAFF
READ_ONLY
```

### 21.1 Role permissions

`ADMIN`:

- full access.

`EVENT_MANAGER`:

- create and edit events,
- manage ticket pools,
- publish events.

`SALES_MANAGER`:

- view orders,
- view tickets,
- process refunds,
- export reports.

`DOOR_STAFF`:

- access only to check-in panel,
- scan QR codes,
- view scan result,
- no access to financial data.

`READ_ONLY`:

- view reports and events,
- no write access.

---

## 22. Audit logs

The system must store audit logs for important actions.

Audit log examples:

- event created,
- event published,
- event edited,
- event cancelled,
- ticket pool opened,
- ticket pool closed,
- order paid,
- tickets issued,
- ticket checked in,
- refund started,
- refund completed,
- emergency list exported.

Each audit log should contain:

- actor ID,
- actor role,
- action,
- entity type,
- entity ID,
- timestamp,
- metadata.

---

## 23. Proposed data model

### 23.1 `events`

```text
events/{eventId}
  title
  slug
  description
  venueName
  venueAddress
  startAt
  endAt
  salesStartAt
  salesEndAt
  capacityTotal
  soldTotal
  status
  calendarEventId
  createdAt
  updatedAt
  createdBy
  updatedBy
```

### 23.2 `ticket_pools`

```text
ticket_pools/{poolId}
  eventId
  name
  priority
  priceGross
  currency
  capacity
  soldCount
  salesStartAt
  salesEndAt
  activationRule
  manualStatus
  status
  createdAt
  updatedAt
```

### 23.3 `orders`

```text
orders/{orderId}
  eventId
  poolId
  buyerEmail
  quantity
  amountGross
  currency
  status
  paymentProvider
  paymentSessionId
  providerOrderId
  createdAt
  paidAt
  refundedAt
```

### 23.4 `tickets`

```text
tickets/{ticketId}
  eventId
  poolId
  orderId
  buyerEmail
  shortCode
  qrTokenHash
  status
  issuedAt
  checkedInAt
  checkedInBy
  refundedAt
  cancelledAt
```

### 23.5 `payment_events`

```text
payment_events/{providerEventId}
  provider
  providerEventId
  eventType
  orderId
  processedAt
  processingStatus
  payloadHash
```

### 23.6 `audit_logs`

```text
audit_logs/{logId}
  actorId
  actorRole
  action
  entityType
  entityId
  createdAt
  metadata
```

---

## 24. Automation goals

The system should automate as much as possible:

- event publication to website,
- event creation in Google Calendar,
- ticket pool activation,
- sales closing after sold out,
- ticket issuing after payment,
- ticket email sending,
- refund invalidation of tickets,
- sales reports,
- emergency list generation,
- audit logging.

Manual admin action should be required only for:

- event creation approval,
- manual pool override,
- refund approval,
- event cancellation,
- emergency correction.

---

## 25. Testing plan

### 25.1 Unit tests

Test:

- active pool selection,
- pool activation by date,
- pool activation after previous pool sold out,
- manual pool close,
- manual pool open,
- event sold out logic,
- ticket status transitions,
- invalid ticket states,
- refund status transitions,
- order status transitions.

### 25.2 Integration tests

Test:

- event creation saves correct data,
- event publish creates website-visible event,
- event publish creates Google Calendar event,
- payment webhook marks order as paid,
- payment webhook issues tickets,
- duplicate webhook does not issue duplicate tickets,
- refund invalidates ticket,
- email is queued after ticket issue,
- emergency list export contains only valid expected data.

### 25.3 End-to-end tests

Required E2E scenarios:

1. Admin creates event.
2. Admin creates ticket pools.
3. Admin publishes event.
4. Event appears on website.
5. Event appears in Google Calendar.
6. Buyer purchases one ticket.
7. Payment webhook confirms payment.
8. Ticket is issued.
9. Ticket email is generated.
10. QR scan checks ticket in.
11. Second QR scan is rejected.
12. Admin refunds ticket.
13. Refunded ticket cannot be checked in.
14. Pool sells out and next pool activates.
15. Event sells out and sales close automatically.

### 25.4 Failure tests

Test:

- payment webhook arrives twice,
- payment webhook arrives late,
- user closes browser after payment,
- email sending fails,
- Google Calendar creation fails,
- two users try to buy the last ticket at the same time,
- event is cancelled after tickets were sold,
- scanner has no internet,
- QR cannot be scanned,
- emergency list is used.

### 25.5 Load tests

Minimum load tests before production:

- 50 users opening event page at the same time,
- 50 users attempting to buy tickets at the same time,
- several users attempting to buy last available tickets,
- fast repeated QR scans at entrance.

---

## 26. Production readiness checklist

The system can go live only when:

- test environment works,
- production environment is separated from test environment,
- payment sandbox tests pass,
- webhook signature verification works,
- duplicate webhook processing is safe,
- event capacity cannot be exceeded,
- pool capacity cannot be exceeded,
- ticket QR works,
- second scan of the same ticket is rejected,
- refunded ticket is rejected,
- cancelled ticket is rejected,
- emergency list export works,
- admin panel permissions work,
- door staff cannot access financial data,
- Google Calendar integration works,
- email delivery works,
- audit logs are written,
- refund process is tested,
- event cancellation process is tested,
- backup procedure exists,
- emergency entrance procedure is documented,
- staff has tested the check-in flow on a phone.

---

## 27. Decisions (resolved 2026-06-11)

1. Payment provider: **Stripe** (preceded by demo payment mode, section 31).

2. Ticket quantity per order: **1 to 10**, configurable per event.

3. Refund policy (MVP): **full refunds only** (whole order or selected tickets), **admin-triggered only**, no deadline enforced by the system (club's operational decision). Event cancellation triggers a one-click bulk refund of all paid orders.

4. Ticket format: **HTML email with QR code** (inline PNG). PDF is out of MVP scope.

5. Buyer email verification: **not required before purchase.** The ticket is delivered by email anyway; a typo means no ticket. Admin can correct the email and resend.

6. Capacity reservation: **reserved atomically at order creation** (conditional atomic UPDATE on event and pool counters). A failed/expired order releases capacity.

7. Unpaid order expiration: **20 minutes**, then capacity is released.

8. Google Sheets export: **out of MVP.** Reports are CSV exports from the admin panel.

9. Emergency list: **generated on demand** by admin **plus an automatic CSV snapshot 2 hours before each event**.

Additional clarifications:

- Timezone: **Europe/Warsaw** everywhere. Currency: **PLN** (demo mode uses the virtual currency `DEMO`).
- Sum of pool capacities may exceed `capacityTotal`; the event capacity is always the hard limit.
- Pool and event statuses are **computed at read time** from dates, counters and `manualStatus` — no scheduler involved. A cron job handles only: order expiration, e-mail retry, emergency list snapshot.
- Google Calendar integration is **non-blocking**: a Calendar failure must never block event publication (outbox + retry).
- Buyer e-mails are personal data (GDPR): plan retention/anonymization ~12 months after the event.
- The purchase endpoint must be rate-limited.

---

## 28. Recommended implementation order

### Phase 1 — Specification and design

- Finish this specification.
- Design UI in Claude Design.
- Confirm admin and buyer flows.
- Confirm refund policy.
- Confirm payment provider.

### Phase 2 — Backend foundation

- Create data model.
- Create event CRUD.
- Create ticket pool logic.
- Create event publishing logic.
- Add audit logs.

### Phase 3 — Website and admin MVP

- Public event list.
- Public event details page.
- Admin event editor.
- Admin ticket pool editor.

### Phase 4 — Google Calendar integration

- Create calendar event on publish.
- Update calendar event on edit.
- Handle calendar errors safely.

### Phase 5 — Ticket issuing without real payment

- Test order creation.
- Test ticket issuing.
- Test QR generation.
- Test email preview.
- Test QR scanner.
- Test emergency list export.

### Phase 6 — Payment sandbox

- Add payment provider sandbox.
- Add webhook handling.
- Add idempotency.
- Add paid order flow.
- Add refund sandbox flow.

### Phase 7 — Testing

- Unit tests.
- Integration tests.
- E2E tests.
- Failure tests.
- Load tests.

### Phase 8 — Internal pilot

- Run fake internal event.
- Sell test tickets.
- Scan tickets on a phone.
- Simulate internet failure.
- Simulate refund.
- Simulate sold out event.

### Phase 9 — Soft launch

- First real small event.
- Manual supervision.
- Emergency list printed/exported.
- Technical person available during entrance.

### Phase 10 — Production rollout

- Enable full public sales.
- Monitor logs.
- Review post-event report.
- Fix issues before scaling.

---

## 29. Notes for Claude Design

Use this file as the source of product requirements.

Claude Design should prepare mobile-first screens for:

- public event page,
- buy ticket flow,
- payment success screen,
- payment failure screen,
- ticket email view,
- admin event list,
- admin event editor,
- admin ticket pool editor,
- admin sales view,
- admin refund view,
- QR scanner view,
- valid ticket screen,
- already used ticket screen,
- refunded ticket screen,
- emergency list export view.

---

## 30. Notes for Claude Code

Claude Code should not implement the whole system at once.

Implementation must be split into small steps:

1. Data model.
2. Event CRUD.
3. Ticket pool activation logic.
4. Public event page.
5. Admin event panel.
6. Google Calendar integration.
7. Test order creation.
8. Ticket issuing.
9. QR generation.
10. Check-in scanner.
11. Emergency list export.
12. Payment provider integration.
13. Payment webhook idempotency.
14. Refund flow.
15. E2E tests.
16. Production readiness checks.

Each step should include tests before moving to the next step.

---

## 31. Demo payment mode

### 31.1 Purpose

Before real payments are enabled, the system must support a complete, realistic purchase cycle using a **virtual demo currency** (`DEMO`): order → payment → ticket issuing → check-in → refund → sales and cash-flow reports. This allows full operational testing (reports, refunds, money flows) without touching a real bank or Stripe account. Disabling demo mode is what enables real transactions.

### 31.2 DemoProvider

`DemoProvider` implements the same `PaymentProvider` interface as Stripe and renders an internal "demo checkout" page where the buyer sees the amount in `DEMO` currency and can choose:

- pay (success),
- decline payment,
- simulate timeout,
- abandon,
- optionally: delayed webhook (tests the "user closed the browser after paying" case).

Each action emits an **internal webhook** that travels through exactly the same pipeline as a Stripe webhook: HMAC signature verification, `payment_events` record, idempotency check, order transition, ticket issuing. Only the money source is simulated — the production pipeline is fully exercised.

### 31.3 Global mode switch

- A DB singleton `PaymentConfig` holds `mode: DEMO | LIVE`.
- Only the ADMIN role can switch it; the switch is audit-logged and requires confirmation.
- Switching to LIVE requires configured Stripe live keys and a green production checklist (section 26).
- While in DEMO mode, a visible "TRYB DEMO" banner is shown in the admin panel and on public pages.

### 31.4 Data separation

- `is_demo` flag is stamped on orders, tickets and payment events **at creation time and never changed**.
- Every report (sales, cash flow, refunds) filters by demo/live.
- After switching to LIVE, demo data remains for analysis but never mixes with real data.
- A demo order is always refunded through `DemoProvider` (never calls Stripe), and vice versa — the provider is stored on the order.
- Demo tickets scan normally at the gate (for staff training), but event reports mark them separately.

