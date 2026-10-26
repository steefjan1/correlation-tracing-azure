# Walkthrough: deploy, drive, observe, tear down

Windows PowerShell 5.1 or pwsh 7, with `az`, `azd`, the .NET 8 SDK and Python 3 installed.

## 1. Deploy

```powershell
cd C:\Dev\correlation-tracing-azure
Get-ChildItem .\scripts\*.ps1 | Unblock-File
azd auth login
azd env new correlation
azd env set AZURE_LOCATION swedencentral
azd up
```

Expected duration: 10 to 15 minutes. API Management Consumption activates in two to five minutes; Flex Consumption and App Service are quick.

`scripts/postprovision.ps1` writes `measure/.env` with `GATEWAY_URL` (the APIM endpoint plus `/orders`) and the resource names.

## 2. Drive

```powershell
python .\measure\drive.py all          # happy, mine, slow, failonce, decline
python .\measure\drive.py burst 100
```

The console at `<GATEWAY_URL>/` does the same with buttons and shows both ids per order. The Detail view adds `sameTraceInPaymentWorker` and `sameTraceInNotificationWorker`.

Sampling experiment:

```powershell
azd env set TRACING_SAMPLING_RATIO 0.1
azd provision                          # updates the app settings, apps restart
python .\measure\drive.py burst 100    # writes measure/burst-trace-ids.txt
```

Paste the ids into query 6. Set the ratio back to `1.0` and `azd provision` again before the other screenshots.

## 3. Observe

Log Analytics workspace, Logs blade. Queries in `measure/queries.kql`. Allow two to three minutes for Flex Consumption telemetry to arrive.

| # | Screenshot | Where |
|---|------------|-------|
| 1 | Console with correlation id, trace id, payment and notification status | `<GATEWAY_URL>/` |
| 2 | End-to-end transaction: API Management root, API, provider authorize, Service Bus, PaymentWorker, provider capture, NotificationWorker | Application Insights, Transaction search, filter `POST /orders/api/orders` |
| 3 | Query 2: every span and log line for one correlation id (use a `ticket-...` one from `drive.py mine`) | Log Analytics |
| 4 | Query 3: carriers that reached the provider (traceparent, baggage, header) by URL | Log Analytics |
| 5 | Query 4: FAIL-ONCE orders, deliveries vs traces | Log Analytics |
| 6 | Query 5: dependencies of a SLOW-PAY trace sorted by duration | Log Analytics |
| 7 | Query 6: complete vs partial traces at ratio 0.1 | Log Analytics |
| 8 | Response headers of a POST through the gateway showing `X-Correlation-ID` echoed | browser dev tools or `curl -i` |
| 9 | APIM policy editor with the set-header block | API Management, APIs, Orders, inbound policy |
| 10 | Service Bus message in Service Bus Explorer (peek) with `CorrelationId` application property and `Diagnostic-Id` | Service Bus, topic, subscription, Service Bus Explorer (needs Azure Service Bus Data Receiver on your own account) |

## 4. Tear down

```powershell
azd down --purge
```

API Management Consumption and Flex Consumption bill per call; the App Service plan (B1) bills per hour.

## Measured on 2026-10-25 (swedencentral)

| Scenario | Result |
|---|---|
| POST via API Management | 783 to 1,021 ms warm, 8,986 ms first call (Flex cold start) |
| Happy path | both workers in the API's trace, done before the 0.8 s poll |
| Client-supplied id | `ticket-34d199` kept by the gateway and echoed |
| SLOW-PAY | POST 5,803 ms; notification Sent at 0.9 s, payment Captured at 6.5 s |
| FAIL-ONCE | Captured at 0.7 s after one redelivery |
| DECLINE | 402, nothing published |
| Sampling run 1 (APIM 100 %, API 1.0) | 100 traces, 100 complete |
| Sampling run 2 (APIM 100 %, API 0.1) | 100 traces, 100 complete: the root decides |
| Sampling run 3 (APIM 10 %, API 0.1) | 7 complete, 93 partial; APIM kept 7, API 15, workers 100 |

## Known gaps

- The payment provider endpoint is anonymous. It stands in for an external system; a real one would authenticate and would not run in your own Functions app.
- Baggage received on an HTTP-triggered Function is not restored into `Baggage.Current` by the isolated worker; the provider therefore reads `X-Correlation-ID` explicitly. If a future worker version restores it, query 3 will show `baggage = true` and the code still works.
- No origin lock on the App Service: API Management Consumption has no static outbound IP and no VNet, so the API hostname is reachable directly. Front Door or APIM Standard v2 with a private backend fixes that and is out of scope here.
- The Functions host in OpenTelemetry mode records every trigger regardless of the sampled flag in `Diagnostic-Id`; the worker-side sampler only applies to spans the worker creates. See the sampling section of the post.
- Logs: the correlation id reaches log lines through a logging scope (`CorrelationId`). In the workspace tables look in `Properties["CorrelationId"]` for traces and `Properties["correlation.id"]` for requests and dependencies.
