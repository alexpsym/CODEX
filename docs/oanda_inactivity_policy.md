# OANDA Australia inactivity widget

Policy checked 27 October 2026 against official public documentation:

- [Australia inactivity FAQ](https://help.oanda.com/au/en/faqs/inactivity-fees.htm)
- [Australia charges](https://www.oanda.com/au-en/trading/our-charges/)
- [v20 account definitions](https://developer.oanda.com/rest-live-v20/account-df/)

The FAQ describes inactivity generally as "12 months" and explicitly designates
an account inactive after **365 days** without an active/open trade. This widget
uses 365 elapsed days from the last verified qualifying live ORDER_FILL, including
across leap years; it does not substitute twelve calendar months. Actual open
trades/positions exempt the monitored account. Pending unfilled orders do not.
The latest fill includes trading reductions/closures as well as openings.
Deposits, withdrawals, transfers, financing, fee postings, login and API polling
do not reset trading activity. The FAQ starts a never-traded account's period at
its first deposit. The existing available history does not establish a verified
first deposit, so this widget leaves an unknown anchor rather than using account
creation or inventing a date. Account creation only bounds the existing fill scan.

Monthly assessments occur on the third-last Monday-Friday date. No public-holiday
adjustment is assumed. All displayed policy dates and day counts use
Australia/Brisbane. Eligibility and assessment are different: a synthetic threshold
of 5 November 2026 has its first eligible assessment on 26 November 2026. This
example does not identify the user's actual trade. Eligibility after a month's
assessment advances to the following month. On an assessment date the countdown
says "Assessment due today"; on the next Brisbane date it advances to the next
eligible monthly assessment. No exact intraday charge time or withdrawal cutoff
is known or implied. The widget estimates dates; it never verifies that a broker
charge was made or waived. Historical first eligibility remains separate from
the rolling next assessment, including when a browser tab resumes after suspension.

The monthly maximum is **AUD 20 per client**, converted by OANDA to sub-account
home currencies. It is not a guaranteed charge or an AUD 20 fee for each
sub-account. The configured live account's summary `balance` and `currency` are
authoritative for this widget; NAV, margin and reconstructed journal balances
are not substitutes. Decimal validation rejects missing, malformed, nonfinite
and boolean balances, and does not round small positive balances to zero.
A separately verified zero balance suppresses urgent risk/countdown even when
history cannot be resolved, without erasing trading history. Negative/unknown
balances are not treated as verified zero. Positive balances restore the normal
assessment display after the normal refresh/cache cycle, without suggesting an
immediate fee or a trading reset. An upstream failure replaces a cached zero
assurance with unavailable data and preserves existing HTTP error behavior.

Scope is only the configured live account, identified in payload and expanded
widget details. An authoritative summary for another account is rejected. Zero
on this account says nothing about balances in other sub-accounts. There is no
account discovery, demo substitution or additional multi-account history scan.
The existing 45-second success / 10-second error cache is scoped to live mode,
base URL, account identifier and credential identity (credentials are never
returned). Existing dashboard polling/timers remain unchanged; no faster polling,
new service or parallel widget is introduced. First eligible and next assessment
fields supplement the historical `earliest_fee_date` compatibility field;
`seconds_until_threshold` remains available but is not the primary countdown.

Verification uses only the five Job 8 selectors in
`tests/test_master_service_oanda_inactivity.py`, with canonical OANDA functions loaded from a temporary source copy,
controlled clocks, mocked account/history transport, isolated caches and the
actual dashboard JavaScript in an existing Node VM harness. Fixture JSON enters
Node through stdin. No real broker account, funds or trading operation is used.

Acceptance after the local LTT service is restarted to load the Python change:
expand the existing widget and compare its balance/home currency and live account
identifier with the account's current state. At zero, confirm calm suppression;
at positive balance, confirm the expected Brisbane assessment date and civil-day
countdown; with unavailable data, confirm no zero-balance assurance. These are
observations only: do not trade or move funds to test transitions. Synthetic tests
cover transitions without waiting for a real assessment or verifying a charge.

Verification checkpoint (27 October 2026 continuation): the fixture runner now
executes actual inline-completing mocked coroutines without an event loop,
propagates results/exceptions, closes the coroutine and rejects unexpected
suspension immediately. Production asyncio is unchanged. The single authorized
continuation invocation completed: calendar/365-day boundary, balance transitions,
open-position/unknown data, and cache/scope nodes passed. The actual JavaScript
node failed at its first expanded-detail assertion: the harness clicks expand,
then expects the explanatory text before invoking the captured one-second render
timer. The later UI transition/rollover/error assertions were not reached and
remain unverified. No assertion was removed or weakened, and no retry occurred.
Final exit code 1; delivery remains blocked. Evidence (stdout, stderr, JUnit,
command, PID and exit code) is retained locally in
`.job8_oanda_continuation_20261027_01/`. Original timeout evidence is preserved.

Final verification (27 October 2026): the remaining actual-JavaScript UI node
passed in 6.86 seconds, exit code 0. The harness invokes only the captured 1000ms
render callback after expanding details and after settling mocked refreshes;
all original assertions and later transitions remain intact. No arbitrary sleep,
real timer or background polling callback is run. Evidence and JUnit are retained
in `.job8_oanda_final_20261027_01/`. Combined with the four earlier passing nodes
in `.job8_oanda_continuation_20261027_01/`, all five Job 8 nodes have passed across
separate invocations. The four passing nodes were not repeated. This supersedes
the preceding incomplete verification checkpoint; original failure logs remain.
Live acceptance has not been performed. Production files were read-only throughout
the fixture continuations.
