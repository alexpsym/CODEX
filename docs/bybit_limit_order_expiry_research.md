# Bybit limit-entry expiry with LTT closed

Verified **2026-10-26 (Australia/Brisbane)**. Repository inspected at `ff81c40a66d9ed64b09c2798b830e809df791b36`.

## Verdict: unsupported on the documented path

No documented exchange-enforced duration or deadline applies to LTT's ordinary `linear` USDT perpetual limit-entry request, for either mainnet demo or ordinary retail live use. The complete [V5 order-create request table](https://bybit-exchange.github.io/docs/v5/order/create-order#request-parameters) has no per-order expiry field. This is a finding about the checked API/product path, not proof about every Bybit product or private entitlement.

**Job 7's native-expiry implementation is blocked by this capability finding. Proceed to Job 8.** Retain the original offline-cancellation requirement in the queue; do not add controls that imply it is supported.

## Documented capabilities and evidence

| Mechanism | Official evidence | Meets the chosen minutes/hours/date requirement? |
| --- | --- | --- |
| Per-order duration/deadline | [Create order](https://bybit-exchange.github.io/docs/v5/order/create-order): full schema inspected through its final BBO fields; no lifetime/deadline parameter. | **No documented support.** Do not invent `expireTime`, `cancelAfter` or `GTD`. |
| GTC | [Manual-order TIF guide](https://www.bybit.com/en/help-center/article/What-Are-Time-In-Force-TIF-GTC-IOC-FOK): rests until filled or cancelled. | No chosen expiry. Closing LTT does not turn GTC into a timer. |
| IOC / FOK | [Same guide](https://www.bybit.com/en/help-center/article/What-Are-Time-In-Force-TIF-GTC-IOC-FOK): IOC immediately cancels the unfilled remainder; FOK immediately fills everything or cancels everything. | Exchange executes these rules without LTT, but they do not wait for a future deadline. IOC fills already made are not undone by remainder cancellation. |
| PostOnly / RPI | [Create order](https://bybit-exchange.github.io/docs/v5/order/create-order): PostOnly cancels if it would immediately execute; RPI is a restricted market-maker order. [Complete TIF enum](https://bybit-exchange.github.io/docs/v5/enum#timeinforce): `GTC`, `IOC`, `FOK`, `PostOnly`, `RPI`; no `GTD`. | Neither supplies the requested timer. RPI is not evidence of ordinary retail order-placement eligibility. |
| Disconnect Cancel All / DCP | [DCP](https://bybit-exchange.github.io/docs/v5/order/dcp): `POST /v5/order/disconnected-cancel-all`; `product` = `OPTIONS` (default), `DERIVATIVES`, `SPOT`; `timeWindow` = integer 3–300 **seconds**. Institutional clients only; the page explicitly excludes VIP clients. Requires private WebSocket `dcp` subscription/heartbeat conditions. | **Not a single-order expiry.** Bybit cancels active orders in the configured product scope after qualifying disconnect, rather than timing one selected entry. No entitlement inferred; nothing enabled. |
| Contract settlement/expiry | [Instrument metadata](https://bybit-exchange.github.io/docs/v5/market/instrument): `deliveryTime` concerns futures delivery/perpetual delisting, with delivery metadata also present for options. | Instrument lifecycle, not a user-selected deadline on an entry order. |
| Demo versus live | [Demo service](https://bybit-exchange.github.io/docs/v5/demo): supports `/v5/order/create`, uses `api-demo.bybit.com`, and says basic trading rules match real trading. | No additional expiry field is documented. Demo's statement that orders are kept seven days does **not** establish configurable lifetime or precisely define active-order cancellation versus record retention. |

Schema audit included routing/quantity/price, spot-only settings, slippage, conditional triggers, `timeInForce`, position/order identifiers, TP/SL, reduce/close flags, SMP/MMP and BBO fields. Neither an absolute deadline nor relative duration appears. Authentication timestamps/receive windows concern request validity, not the lifetime of an accepted order.

The user's recollection about manually placed orders is **not verified for USDT perpetuals**: the official manual-order guide covers perpetual trading and describes GTC/IOC/FOK, not a chosen future expiry. No private UI/API or account was inspected.

## Repository observations: the actual LTT path

These are static source observations, not broker acceptance results:

- [`calculator.js:1532`](../render/static/calculator.js#L1532) sends account, symbol, side, order type, entry, quantity and absolute SL/TP to `/api/calculator/submit`; no entry deadline is sent. [`calculator_submit():32265`](../render/master_service.py#L32265) builds a canonical payload and routes `asset="crypto"` to `_place_bybit_order()`.
- [`_place_bybit_order():24421`](../render/master_service.py#L24421) defaults `trade_mode` to `linear` (the calculator canonical payload does not supply another mode). The scoped USDT perpetual therefore uses `category="linear"`; the category alone also encompasses other contracts, so the symbol/product matters.
- [`24502`](../render/master_service.py#L24502) selects explicit demo/live credentials. Read-only resolver inspection at [`bybit_credentials.py:125`](../bybit_credentials.py#L125) confirms demo defaults to `https://api-demo.bybit.com`, live to `https://api.bybit.com`, with configured URL overrides. This calculator route does not select the resolver's separate testnet mode. No environment values or account privileges were queried.
- [`24560`](../render/master_service.py#L24560) builds `category`, `symbol`, `side`, `orderType`, `qty`, `orderLinkId`; a limit adds `price` and `timeInForce="GTC"`, while market uses `"IOC"`. [`24731`](../render/master_service.py#L24731) sends the body through `_bybit_signed_post()` to **`POST /v5/order/create`**. Signing/receive-window construction is at [`19794`](../render/master_service.py#L19794).
- [`24621`](../render/master_service.py#L24621) attaches `takeProfit`/`stopLoss`, `tpslMode="Full"`, `tpOrderType="Market"`, `slOrderType="Market"`. A protected limit is recorded as attached at creation with verification pending until fill ([`24882`](../render/master_service.py#L24882)); these protective settings are not expiry instructions.
- Read, **not executed**: [`test_submit_routes_to_existing_order_helpers`](../tests/test_master_service_calculator_api.py#L1126) and [`test_place_bybit_limit_order_with_attached_tpsl_does_not_wait_for_position_entry`](../tests/test_master_service_bybit_order_modes.py#L293). Their mocked expectations support the routing/protection trace, not live support for expiry.

Existing similarly named features are different: `calculator.js:831`/`:865` handles a local trendline plan's `expiry_at_ms`; `master_service.py:24111` handles an options contract date; cache `expires_at` values concern freshness. `_poll_pending_webhook_invalidations():25990` processes local WAITING webhook records, and `_parse_limit_cancel_settings():24038` reads price-distance/percentage settings. None is a documented deadline on this placed GTC entry.

## Shutdown responsibility and limits

**Inference from the above:** no documented mechanism schedules this entry's cancellation at the requested future time when LTT and the computer are off. GTC remains exchange-held, without the requested timer. An LTT worker or Windows task requires a running host; a cloud/third-party scheduler requires another running service and authorised cancel requests. DCP can execute at Bybit after an eligible, configured connection is lost, but its disconnect trigger, privileges and broad scope do not meet this requirement. No workaround was provisioned.

Because the applicable expiry mechanism is absent, there are no supported duration units, deadline timezone/UTC representation or Brisbane conversion to implement. No scheduled-expiry semantics can be promised for partial fills, remainder cancellation, or preservation of filled exposure and attached SL/TP. Existing protective payload behavior was only traced, not changed or live-validated.

No material conflict was found among the scoped order schema, enum and manual TIF guide. To reopen Job 7, the smallest useful evidence is written official Bybit confirmation naming the exact supported endpoint/parameter for **retail USDT perpetuals**, demo/live availability, deadline encoding and partial-fill/protection semantics. A remembered UI option would first need its exact product/account and official explanation. Documentation alone would still not prove controlled broker acceptance; no speculative request or trial order is justified by this report.

Verification used **six opened official documentation pages and two search queries**; no changelog expansion was needed. Tests, builds, deployments, account calls, orders, amendments/cancellations, imports, restarts and production-data operations: **zero**. All implementation/configuration files remained read-only. Job 4's live fill check and Job 5 price movement remain pending; Job 5's three user-performed live checks are recorded as passed. The cancelled ETHUSDT fee job remains cancelled.
