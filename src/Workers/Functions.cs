using System.Diagnostics;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Azure;
using Azure.Data.Tables;
using Azure.Messaging.ServiceBus;
using Microsoft.Azure.Functions.Worker;
using Microsoft.Azure.Functions.Worker.Http;
using Microsoft.Extensions.Logging;

namespace CorrelationTracing.Workers;

public record OrderPlaced(string OrderId, string CustomerId, string Sku, decimal Amount);
public record PaymentRequest(string OrderId, string Sku, decimal Amount);

/// <summary>
/// Stands in for an external payment provider. It is deliberately a plain HTTP endpoint so the
/// sample can show which context actually arrives on the wire: traceparent, baggage, X-Correlation-ID.
/// SLOW-PAY takes five seconds, DECLINE returns 402, anything else authorizes immediately.
/// </summary>
public sealed class PaymentProvider(ILogger<PaymentProvider> log)
{
    [Function("PaymentProvider")]
    public async Task<HttpResponseData> Run(
        [HttpTrigger(AuthorizationLevel.Anonymous, "post", Route = "payments/{action}")] HttpRequestData req,
        string action, CancellationToken ct)
    {
        var hasTraceparent = req.Headers.TryGetValues("traceparent", out _);
        var hasBaggage = req.Headers.TryGetValues("baggage", out _);
        var correlationId = req.Headers.TryGetValues(Correlation.Header, out var v) ? v.FirstOrDefault() : null;

        // Baggage from the HTTP header is not restored for an isolated-worker HTTP trigger, so take
        // the business id from the explicit header. This is exactly why that header exists.
        if (!string.IsNullOrEmpty(correlationId)) Correlation.Set(correlationId);
        Activity.Current?.SetTag("context.traceparent.arrived", hasTraceparent);
        Activity.Current?.SetTag("context.baggage.arrived", hasBaggage);
        Activity.Current?.SetTag("context.correlation_header.arrived", correlationId is not null);

        using var scope = log.BeginScope(new Dictionary<string, object?> { ["CorrelationId"] = correlationId });
        var body = await JsonSerializer.DeserializeAsync<PaymentRequest>(req.Body, Json, ct)
                   ?? throw new InvalidOperationException("empty body");

        log.LogInformation("Provider {Action} for {OrderId}: traceparent={Traceparent} baggage={Baggage} correlationHeader={Header}",
            action, body.OrderId, hasTraceparent, hasBaggage, correlationId is not null);

        if (body.Sku == "SLOW-PAY") await Task.Delay(TimeSpan.FromSeconds(5), ct);   // the 5,200 ms the waterfall should expose

        var res = req.CreateResponse(body.Sku == "DECLINE" && action == "authorize" ? HttpStatusCode.PaymentRequired : HttpStatusCode.OK);
        await res.WriteAsJsonAsync(new { action, body.OrderId, referenceId = Guid.NewGuid().ToString("N")[..10], correlationId }, ct);
        return res;
    }

    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
}

/// <summary>
/// Consumes the 'payment' subscription. The SDK carries traceparent in Diagnostic-Id, so this
/// function already runs inside the API's trace. Baggage does not cross Service Bus, so the
/// business id is restored from the application property the API set by hand.
/// FAIL-ONCE throws on the first delivery; the redelivery is a new span in the same trace.
/// </summary>
public sealed class PaymentWorker(TableClient table, IHttpClientFactory http, ILogger<PaymentWorker> log)
{
    [Function(nameof(PaymentWorker))]
    public async Task Run(
        [ServiceBusTrigger("orders", "payment", Connection = "ServiceBus")] ServiceBusReceivedMessage message,
        CancellationToken ct)
    {
        var order = message.Body.ToObjectFromJson<OrderPlaced>(Json)!;
        var correlationId = Restore(message);
        using var scope = log.BeginScope(new Dictionary<string, object?> { ["CorrelationId"] = correlationId });

        var traceId = Activity.Current?.TraceId.ToString();
        log.LogInformation("PaymentWorker {OrderId} delivery {Delivery} trace {TraceId}", order.OrderId, message.DeliveryCount, traceId);

        await table.UpdateEntityAsync(new TableEntity("orders", order.OrderId)
        {
            ["PaymentTraceId"] = traceId, ["PaymentDeliveryCount"] = message.DeliveryCount
        }, ETag.All, TableUpdateMode.Merge, ct);

        if (order.Sku == "FAIL-ONCE" && message.DeliveryCount == 1)
            throw new TimeoutException($"Simulated failure on first delivery for {order.OrderId}");

        var client = http.CreateClient("payments");
        var res = await client.PostAsJsonAsync("api/payments/capture", new PaymentRequest(order.OrderId, order.Sku, order.Amount), ct);
        res.EnsureSuccessStatusCode();

        await table.UpdateEntityAsync(new TableEntity("orders", order.OrderId) { ["PaymentStatus"] = "Captured" },
            ETag.All, TableUpdateMode.Merge, ct);
        log.LogInformation("Payment captured for {OrderId}", order.OrderId);
    }

    internal static string? Restore(ServiceBusReceivedMessage message)
    {
        var id = message.ApplicationProperties.TryGetValue(Correlation.MessageProperty, out var o) ? o?.ToString() : message.CorrelationId;
        if (!string.IsNullOrEmpty(id)) Correlation.Set(id);
        Activity.Current?.SetTag("messaging.message.delivery_count", message.DeliveryCount);
        return id;
    }

    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
}

/// <summary>Consumes the 'notification' subscription: same message, second branch of the same trace.</summary>
public sealed class NotificationWorker(TableClient table, ILogger<NotificationWorker> log)
{
    [Function(nameof(NotificationWorker))]
    public async Task Run(
        [ServiceBusTrigger("orders", "notification", Connection = "ServiceBus")] ServiceBusReceivedMessage message,
        CancellationToken ct)
    {
        var order = message.Body.ToObjectFromJson<OrderPlaced>(new JsonSerializerOptions(JsonSerializerDefaults.Web))!;
        var correlationId = PaymentWorker.Restore(message);
        using var scope = log.BeginScope(new Dictionary<string, object?> { ["CorrelationId"] = correlationId });

        using (var span = Telemetry.Source.StartActivity("SendEmail"))
        {
            span?.SetTag("order.id", order.OrderId);
            await Task.Delay(200, ct);   // pretend to talk to an email service
        }

        await table.UpdateEntityAsync(new TableEntity("orders", order.OrderId)
        {
            ["NotificationStatus"] = "Sent", ["NotificationTraceId"] = Activity.Current?.TraceId.ToString()
        }, ETag.All, TableUpdateMode.Merge, ct);
        log.LogInformation("Notification sent for {OrderId}", order.OrderId);
    }
}
