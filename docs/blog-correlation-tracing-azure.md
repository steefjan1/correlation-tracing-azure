---
title: "Correlation IDs and OpenTelemetry on Azure: One Request, Two Identities"
seo_title: "Correlation IDs and OpenTelemetry on Azure: Two Identities"
slug: correlation-ids-opentelemetry-azure-two-identities
focus_keyphrase: "correlation IDs and OpenTelemetry"
meta_description: "Correlation IDs and OpenTelemetry solve different problems. I built an order flow on Azure that carries both across API Management, App Service, Service Bus and Functions, and measured where each one survives."
excerpt: "A customer quotes an order number; an engineer has a trace id. Correlation IDs and OpenTelemetry are two identities for one request, with different owners and lifetimes. I built an order flow on Azure that carries both through API Management, App Service, Service Bus and Functions, and measured where each survives: HTTP, messaging, retries, slow dependencies and sampling. The sampling result surprised me."
categories: [Azure, Architecture, Integration]
tags: [OpenTelemetry, Distributed Tracing, Correlation ID, Application Insights, Azure Service Bus, Azure Functions, API Management, Observability, Azure App Service, Cloud Perspectives]
status: draft
companion_repo: https://github.com/steefjan1/correlation-tracing-azure
---

A customer calls. Their order went through, the confirmation email never came, and the money left their account. The service desk has an order number. The engineer on call has Application Insights. Between those two people sits a question I have watched teams get wrong for years: which identifier connects what the customer knows to what the platform recorded?

Most teams answer "the correlation ID" and then build one thing, when they need two. Correlation IDs and OpenTelemetry traces solve different problems, for different readers, with different lifetimes. The trace ID is the platform's: minted per request, read by an engineer in a waterfall, gone from the conversation after a few seconds. The correlation ID is the business's: it belongs to a workflow that may span days, it is what the customer can quote, and it must survive hops that no tracing SDK knows about. You need both, they must travel together, and they are not the same field.

So I built an order flow on Azure that carries both and measured where each one survives: through API Management, into an API on App Service, out to a payment provider over HTTP, across a Service Bus topic into two Function workers, and back into Application Insights. The code is in [the companion repo](https://github.com/steefjan1/correlation-tracing-azure), `azd up` away.

[DIAGRAM 1: images/architecture.png | alt: "Architecture for correlation IDs and OpenTelemetry on Azure: API Management, Order API on App Service, payment provider, Service Bus topic, two Function workers, Table Storage and Application Insights, with the context carried on each hop"]

## Four things that get called the correlation ID

Before the code, the vocabulary, because the confusion starts there.

[DIAGRAM 3: images/identities.png | alt: "Table comparing correlation ID, trace ID, span ID and message ID by owner, scope, lifetime, reader and carrier"]

The **trace ID** is the 32-hex W3C identifier in the `traceparent` header. The **span ID** is the 16-hex identifier of one operation inside that trace. OpenTelemetry owns both, every SDK forwards them, and nobody should ever type one into a customer email. The **message ID** belongs to the broker; Service Bus uses it for duplicate detection and nothing else. The **correlation ID** is the one the business owns. It is minted at the edge or supplied by the caller, it lives as long as the workflow, and it is what the service desk searches for.

Once you separate them, the design follows: the trace rides on the platform's carriers and you write no code for it; the correlation ID rides on carriers you choose, and you write a little code for it, exactly once per kind of hop.

## What the sample does

An order comes in through API Management (Consumption tier). A policy keeps the caller's `X-Correlation-ID` if there is one and mints `gw-<12 hex>` if there is not, then echoes it on the response so the caller can quote it. The gateway runs in W3C correlation mode, so its own span is the root of the trace and the `traceparent` it forwards is the one everything downstream continues.

The Order API on App Service runs the Azure Monitor OpenTelemetry distro. A small middleware reads the header, puts the value into OpenTelemetry baggage, tags the current span with `correlation.id`, and opens a logging scope with the same value. A processor copies the baggage value onto every span that starts afterwards, so one Kusto filter on `customDimensions["correlation.id"]` finds all of them. The API writes the order to Table Storage, calls the payment provider over HTTP, and publishes `OrderPlaced` to a Service Bus topic.

Two Function workers on Flex Consumption consume the topic: `PaymentWorker` captures the payment through the provider, `NotificationWorker` pretends to send an email. A third function plays the external payment provider and reports, on its own span, which of the three carriers reached it: `traceparent`, `baggage`, `X-Correlation-ID`.

Every component writes to one workspace-based Application Insights resource. Managed identities everywhere; local auth is off on Service Bus and shared keys are off on storage. The same stance as in [the Azure Functions MCP server post](https://sjwiggers.com/2026/10/22/azure-functions-mcp-server-four-rules/): the role list is the authority.

[SCREENSHOT 1: orders console | alt: "Orders console showing two orders with the same client-supplied correlation ID ticket-4811 and two different trace IDs, plus gateway-minted gw- ids"]

The console makes the thesis visible without a query. Two orders placed with `X-Correlation-ID: ticket-4811` show the same correlation ID and two different trace IDs. One business identity, two platform traces. The rows below them, placed without a header, carry gateway-minted `gw-` ids instead.

## Where each identity survives

[DIAGRAM 2: images/trace.png | alt: "One order as a trace waterfall across API Management, Order API, Table Storage, payment provider, Service Bus and two workers, with the carriers listed per hop"]

### HTTP hops: both travel, for free and almost free

Gateway to API, API to provider, worker to provider. On every HTTP hop the SDK writes `traceparent` and the `baggage` header, and the receiving side restores both. The trace continues without a line of code. The correlation ID also travels, in baggage, which is why the middleware puts it there.

Almost free, because of the receiver that is not yours. The payment provider is a stand-in for a system that has never heard of OpenTelemetry. It gets the `X-Correlation-ID` header too, added by a `DelegatingHandler` that reads baggage. That header is the fallback for every integration partner, legacy service and log file in the chain. Baggage is for your own services; the header is for everyone else.

[SCREENSHOT 4: query 3 output | alt: "Kusto result showing which carriers reached the payment provider: traceparent, baggage and X-Correlation-ID per endpoint"]

### The message hop: the trace travels, the correlation ID does not

Service Bus is where the two identities part ways. The Azure SDK stamps `Diagnostic-Id` on every message with the current `traceparent`, and the Functions trigger reads it back, so `PaymentWorker` and `NotificationWorker` run inside the API's trace. The `paymentTraceId` and `notificationTraceId` columns in the Orders table exist to prove it per order, and on every normal order in the measured run both matched the API's trace ID.

Baggage does not cross Service Bus. There is no `baggage` property on the message and the SDK does not invent one. If the correlation ID lives only in baggage, it dies at the topic. The fix is one line on the sending side, `message.ApplicationProperties["CorrelationId"] = correlationId`, and one on the receiving side to restore it into baggage before any downstream call. The sample also sets the AMQP `CorrelationId` property, because Service Bus Explorer and most tooling display that one.

[SCREENSHOT 2: Application Insights end-to-end transaction | alt: "End-to-end transaction in Application Insights spanning API Management, the Order API, Service Bus and two Function workers under one operation id"]

### Redelivery: new span, same trace

`FAIL-ONCE` makes the payment worker throw on its first delivery. Service Bus redelivers; the second attempt is a new request span with the same operation id and `messaging.message.delivery_count = 2`. In the measured run the order was Captured 0.7 seconds after acceptance, so the abandon and the redelivery fit inside the first poll interval. Query 4 in `measure/queries.kql` lists orders with more than one delivery and confirms they sit in a single trace. Retries are not a new story, they are the second chapter of the same one.

### The slow dependency: the waterfall answers the question

`SLOW-PAY` makes the provider sleep five seconds. The POST through the gateway took 5,803 ms, and nothing inside the API explains it until you open the dependency span. The same order shows the two branches of one message behaving independently: the notification worker reported Sent at 0.9 seconds while payment stayed Pending until 6.5 seconds, because capture hits the same delay. Query 5 sorts the dependencies of one trace by duration; the provider call is the first row and everything else is double digits. That is the whole promise of distributed tracing, and it only works because the trace survived the HTTP hop.

### Sampling: three samplers, three answers

The last measurement is the one people skip, and it produced the most useful numbers in this post. I sent a burst of 100 orders three times and counted, per trace, how many of the six expected request spans arrived: gateway, API, provider authorize, PaymentWorker, provider capture, NotificationWorker.

| Run | Gateway sampling | API ratio | Traces with any span | Complete | Partial | Gateway kept | API kept | Workers kept |
|---|---|---|---|---|---|---|---|---|
| 1 | 100 % | 1.0 | 100 | 100 | 0 | 100 | 100 | 100 |
| 2 | 100 % | 0.1 | 100 | 100 | 0 | 100 | 100 | 100 |
| 3 | 10 % | 0.1 | 100 | 7 | 93 | 7 | 15 | 100 |

[SCREENSHOT 3: query 6 output for run 3 | alt: "Sampling result at 10 percent: 100 traces, 7 complete, 93 partial, with API Management keeping 7, the API 15 and the workers 100"]

Run 2 is the first lesson. I set the API's sampler to ten percent and nothing changed, because the API is no longer the root of the trace. The gateway is, it keeps everything, and it forwards `traceparent` with the sampled flag set. A parent-respecting sampler downstream does what the parent says. Once a gateway sits in front, the sampling decision has moved to the gateway whether anyone intended it or not.

Run 3 is the second lesson, and it splits three ways. API Management kept 7 of 100, its own ten percent. The API kept 15: the 7 the gateway marked as sampled, plus 8 of its own on traces the gateway had dropped. The Azure Monitor sampler honours a positive decision from the parent and makes its own on a negative one. And the Function workers kept all 100. In the isolated worker's OpenTelemetry mode the request spans are emitted by the Functions host, not by my code, and the host recorded every trigger regardless of the flag that arrived in `Diagnostic-Id`. The `ParentBasedSampler` I configured governs only the spans the worker itself starts.

So 93 of 100 traces were partial, and the smallest ones were exactly four spans: the workers, and nobody else. If your plan is "sample at the edge to save cost", on this stack the edge saves cost at the edge and the back end keeps paying full price, while every dropped trace turns into an orphaned tail that no waterfall will ever show you.

The honest recommendation is narrower than the usual advice. Either do not head-sample at all and control cost with ingestion caps and retention, or sample at the first component you fully control and verify, with exactly this query, that every downstream hop follows. On this stack today, the Functions host does not.

## Where this is the wrong answer

A correlation ID per request. If the gateway mints a new value on every call, you have a second trace ID with a worse format. The value only earns its name when the client can supply one for the whole workflow: the order number, the support ticket, the batch run. The gateway mints a value for callers who do not, and the response header tells them what it was.

Baggage as a data bus. Baggage is sent on every outgoing request, to every destination, including the payment provider you do not own. Put one identifier in it, never a customer ID, never an email address, never a token. The processor in the sample copies exactly one key for exactly that reason.

Correlation ID in the message body. It works until a consumer deserializes into a type that does not have the field, or a dead-letter viewer shows you properties and not payloads. Application properties are visible to the broker, to tooling, and to subscription filters. The body is for the business event.

Sampling anywhere but the root. Run 3 is the proof. If three components make three decisions, the only traces you can rely on are the ones all three happened to keep.

## What the first run against Azure taught me

### The measured run

`python measure/drive.py all` through the API Management endpoint in swedencentral:

- POST through the gateway: 783 to 1,021 ms warm; 8,986 ms on the very first order, which is Flex Consumption cold-starting the payment provider.
- Happy path: both workers reported the API's trace id and had finished before the first poll at 0.8 s.
- Client-supplied `X-Correlation-ID: ticket-34d199`: kept by the gateway, echoed on the response, present on every span.
- `SLOW-PAY`: POST 5,803 ms; notification Sent at 0.9 s, payment Captured at 6.5 s.
- `FAIL-ONCE`: Captured at 0.7 s after one redelivery.
- `DECLINE`: 402, nothing published, both worker trace columns empty.
- Sampling: the three-run table above.

### What broke first

1. A binary mismatch inside the Functions worker. Pinning `Microsoft.Azure.Functions.Worker` 2.0.0 next to `Microsoft.Azure.Functions.Worker.OpenTelemetry` 1.1.0 pulled a newer `Worker.Core` than `Worker.Grpc`, and every invocation died with `MissingMethodException: DefaultTraceContext..ctor`. The three functions listed fine; they just could not be invoked. Letting NuGet take the latest of `Worker`, `Worker.Sdk` and `Worker.OpenTelemetry` together fixed it. [TODO: the versions that NuGet picked]
2. Resource names. My environment name was 19 characters, the Bicep took the first 12, and the cut landed on a hyphen. Service Bus refuses a double hyphen; eight other resource types accepted it. Strip the hyphens before you cut.
3. The console called `/api/orders` as an absolute path and got 404 from the gateway, because behind API Management the app lives under `/orders/`. Every path in a page served through a gateway has to be resolved relative to the page, and `location.pathname` plus a slash is not enough once someone opens `/orders/index.html`. `new URL('api/orders', location.href)` is.
4. Telemetry from a brand-new workspace took close to ten minutes to show up on the first run, long enough to suspect the exporters. They were fine.
5. On Windows arm64, ten Python threads creating the default SSL context at once produced `ssl.SSLError: malloc failure`. One warm-up request before the thread pool fixed it. Not Azure's fault, but it cost a run.

None of these is about correlation or tracing. All of them are the kind of thing that stands between a diagram and a measurement, which is why I keep deploying before I write. The same held for [the scalable solutions reference architecture](INTERNAL-LINK: scalable-solutions post URL) a few days earlier.

## Closing

Correlation IDs and OpenTelemetry are not competitors and not synonyms. OpenTelemetry gives the engineer a trace that crosses HTTP and Service Bus without any code, as long as nobody breaks the parent chain. The correlation ID gives the service desk, the customer and the auditor a handle on the workflow, and it needs one header for HTTP, one application property for messaging, and one processor to make it searchable. Build both, keep them apart, decide sampling once at the root, and the phone call at the top of this post takes one query instead of an afternoon.

The repo is at [github.com/steefjan1/correlation-tracing-azure](https://github.com/steefjan1/correlation-tracing-azure). `azd up`, run `measure/drive.py`, flip the sampling ratios, and `azd down --purge` when you are done.

## Sources

- W3C, [Trace Context](https://www.w3.org/TR/trace-context/) and [Baggage](https://www.w3.org/TR/baggage/)
- OpenTelemetry, [Context propagation](https://opentelemetry.io/docs/concepts/context-propagation/) and [Sampling](https://opentelemetry.io/docs/concepts/sampling/)
- Microsoft Learn, [Azure Monitor OpenTelemetry for .NET applications](https://learn.microsoft.com/azure/azure-monitor/app/opentelemetry-enable?tabs=aspnetcore)
- Microsoft Learn, [Sampling in Application Insights with OpenTelemetry](https://learn.microsoft.com/azure/azure-monitor/app/opentelemetry-configuration?tabs=aspnetcore#enable-sampling)
- Microsoft Learn, [Use OpenTelemetry with Azure Functions](https://learn.microsoft.com/azure/azure-functions/opentelemetry-howto)
- Microsoft Learn, [Distributed tracing and correlation through Service Bus messaging](https://learn.microsoft.com/azure/service-bus-messaging/service-bus-end-to-end-tracing)
- Microsoft Learn, [Integrate Azure API Management with Application Insights](https://learn.microsoft.com/azure/api-management/api-management-howto-app-insights)
- Microsoft Learn, [API Management policy reference: set-header](https://learn.microsoft.com/azure/api-management/set-header-policy)
- Microsoft, [Azure architecture icons](https://learn.microsoft.com/azure/architecture/icons/)
