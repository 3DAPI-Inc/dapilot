# 3DAPI public API surfaces

Read from the live contract (`dapi.py ops`) when in doubt. This file adds the
two things the contract does not carry: scopes and gates.

## Two surfaces, chosen by the key's scopes

A key carries a list of permissions. `FULL` is a superuser and must never be
in an agent's environment. Rate limit is 500 requests per 60 seconds per key,
reported in `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.

**Designer** (our shop: we publish designs, customers order them)

| Operation | Method and path | Scope | Gate |
|---|---|---|---|
| getMe | GET /v1/me | any key | auto |
| uploadQuoteFile | POST /v1/quotes/upload_file | PRICING | auto |
| createQuote | POST /v1/quotes | PRICING | auto |
| listQuotes, getQuoteByPublicId, getQuoteStatus | GET /v1/quotes... | PRICING | auto |
| listOrders, getOrderById, listFilaments, getShippingRates | GET /v1/designer/orders... | ORDER | auto |
| estimateShipping | POST /v1/designer/orders/shipping/estimate | ORDER | auto (read-only in effect) |
| createOrder | POST /v1/designer/orders | ORDER | propose |
| payOrder | POST /v1/orders/{orderId}/pay | ORDER | propose |

Designer Orders routes need an approved account. Print-farm keys get 403 on
quotes.

**Print Farm** (fulfilment; only if our shop also prints)

| Operation | Method and path | Scope | Gate |
|---|---|---|---|
| listPrintFarmOrders, getPrintFarmOrder | GET /v1/print-farm/orders... | READ_ORDERS | auto |
| updatePrintFarmOrderStatus | PATCH /v1/print-farm/orders/{orderId}/status | WRITE_ORDERS | propose |
| listPrintFarmSkus, getPrintFarmSku, designs, extras, materials | GET /v1/print-farm/... | SKU | auto |
| listPrintFarmStocks, getPrintFarmStock, listPrintFarmStockLevels, getPrintFarmStockLevel | GET | READ_STOCK | auto |
| addPrintFarmStockLevelItemsMade | PATCH /v1/print-farm/stock-levels/{id}/items-made | WRITE_STOCK | propose |
| listPrintFarmCourierOptions | GET /v1/print-farm/courier-options | any key | auto |

## Not on any surface

- Creating a design (listing). Nothing in the public API does this today.
  `publish-listing` writes the payload and stops at a human gate until a
  route exists.
- Webhooks: `POST /v1/webhooks/stripe` is Stripe's, not ours to call.

## Error shape

Every non-2xx returns `ApiErrorResponse` (`statusCode`, `message`, and
sometimes `error`). 400 means the body or params broke the contract, and the
message usually names the field. 403 with "does not include the required
permission" means the key's scopes are wrong for the route.
