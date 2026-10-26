using System.Diagnostics;
using Azure.Data.Tables;
using Azure.Identity;
using Azure.Messaging.ServiceBus;
using Azure.Monitor.OpenTelemetry.AspNetCore;
using CorrelationTracing.OrderApi;
using OpenTelemetry.Logs;
using OpenTelemetry.Trace;

var builder = WebApplication.CreateBuilder(args);
var cfg = builder.Configuration;

// OpenTelemetry -> Azure Monitor (Application Insights). The distro instruments ASP.NET Core,
// HttpClient and the Azure SDKs; we add the correlation tag processor and a sampling ratio
// that the measurement script flips between 1.0 and 0.1.
var samplingRatio = float.TryParse(cfg["Tracing:SamplingRatio"], out var r) ? r : 1.0f;
builder.Services.AddOpenTelemetry()
    .UseAzureMonitor(o => o.SamplingRatio = samplingRatio)
    .WithTracing(t => t.AddSource(Telemetry.SourceName).AddProcessor(new CorrelationTagProcessor()));
builder.Services.Configure<OpenTelemetryLoggerOptions>(o => { o.IncludeScopes = true; o.ParseStateValues = true; });

var credential = new DefaultAzureCredential();
builder.Services.AddSingleton(new TableClient(new Uri(Req("Storage:TableServiceUri")), "Orders", credential));
builder.Services.AddSingleton(new ServiceBusClient(Req("ServiceBus:fullyQualifiedNamespace"), credential));
builder.Services.AddTransient<CorrelationHeaderHandler>();
builder.Services.AddHttpClient("payments", c => c.BaseAddress = new Uri(Req("PaymentProvider:BaseUrl")))
    .AddHttpMessageHandler<CorrelationHeaderHandler>();
builder.Services.AddHealthChecks();

var app = builder.Build();
await app.Services.GetRequiredService<TableClient>().CreateIfNotExistsAsync();

// Correlation middleware: accept the caller's id (the gateway sets one when the client did not),
// otherwise mint one; put it in baggage, on the span, in the log scope and on the response.
app.Use(async (ctx, next) =>
{
    var id = ctx.Request.Headers[Correlation.Header].ToString();
    var minted = string.IsNullOrWhiteSpace(id);
    if (minted) id = Correlation.NewId();
    Correlation.Set(id);
    ctx.Response.Headers[Correlation.Header] = id;
    var log = ctx.RequestServices.GetRequiredService<ILoggerFactory>().CreateLogger("Correlation");
    using (log.BeginScope(new Dictionary<string, object> { ["CorrelationId"] = id }))
    {
        if (minted) log.LogInformation("No {Header} on the request, minted {CorrelationId}", Correlation.Header, id);
        await next();
    }
});

app.UseDefaultFiles();
app.UseStaticFiles();
app.MapHealthChecks("/health");

app.MapPost("/api/orders", async (OrderRequest body, TableClient table, ServiceBusClient sb,
    IHttpClientFactory http, ILogger<Program> log, CancellationToken ct) =>
{
    if (string.IsNullOrWhiteSpace(body.Sku) || body.Amount <= 0)
        return Results.BadRequest(new { error = "sku and a positive amount are required." });

    var orderId = Guid.NewGuid().ToString("N")[..12];
    var correlationId = Correlation.Current!;
    var traceId = Activity.Current?.TraceId.ToString() ?? "";

    using var span = Telemetry.Source.StartActivity("PlaceOrder");
    span?.SetTag("order.id", orderId);
    span?.SetTag("order.sku", body.Sku);

    // 1. persist (Table Storage dependency span)
    var entity = new TableEntity("orders", orderId)
    {
        ["CustomerId"] = body.CustomerId ?? "anonymous",
        ["Sku"] = body.Sku,
        ["Amount"] = body.Amount,
        ["CorrelationId"] = correlationId,
        ["TraceId"] = traceId,
        ["Status"] = "Placed",
        ["PaymentStatus"] = "Pending",
        ["NotificationStatus"] = "Pending",
        ["CreatedUtc"] = DateTime.UtcNow
    };
    await table.AddEntityAsync(entity, ct);
    log.LogInformation("Order {OrderId} created for sku {Sku}", orderId, body.Sku);

    // 2. synchronous call to the payment provider (HTTP hop: traceparent + baggage + X-Correlation-ID)
    var client = http.CreateClient("payments");
    var sw = Stopwatch.StartNew();
    var auth = await client.PostAsJsonAsync("api/payments/authorize", new { orderId, body.Sku, body.Amount }, ct);
    sw.Stop();
    log.LogInformation("Payment authorize for {OrderId} returned {Status} in {Ms} ms", orderId, (int)auth.StatusCode, sw.ElapsedMilliseconds);
    if (!auth.IsSuccessStatusCode)
    {
        await table.UpdateEntityAsync(new TableEntity("orders", orderId) { ["Status"] = "Declined", ["PaymentStatus"] = "Declined" }, Azure.ETag.All, TableUpdateMode.Merge, ct);
        return Results.Json(new { orderId, correlationId, traceId, status = "Declined" }, statusCode: 402);
    }

    // 3. publish (message hop: the SDK sets Diagnostic-Id = traceparent; baggage does NOT cross,
    //    so the business id goes in an application property by hand)
    var sender = sb.CreateSender("orders");
    var msg = new ServiceBusMessage(BinaryData.FromObjectAsJson(new OrderPlaced(orderId, body.CustomerId ?? "anonymous", body.Sku, body.Amount)))
    {
        MessageId = orderId,
        Subject = "OrderPlaced",
        ContentType = "application/json",
        CorrelationId = correlationId                       // the AMQP correlation-id property too, for tooling that reads it
    };
    msg.ApplicationProperties[Correlation.MessageProperty] = correlationId;
    await sender.SendMessageAsync(msg, ct);
    log.LogInformation("OrderPlaced published for {OrderId}", orderId);

    return Results.Accepted($"/api/orders/{orderId}", new { orderId, correlationId, traceId, status = "Placed" });
});

app.MapGet("/api/orders/{id}", async (string id, TableClient table, CancellationToken ct) =>
{
    try
    {
        var e = (await table.GetEntityAsync<TableEntity>("orders", id, cancellationToken: ct)).Value;
        return Results.Ok(new
        {
            orderId = id,
            correlationId = e.GetString("CorrelationId"),
            traceId = e.GetString("TraceId"),
            status = e.GetString("Status"),
            paymentStatus = e.GetString("PaymentStatus"),
            notificationStatus = e.GetString("NotificationStatus"),
            paymentTraceId = e.GetString("PaymentTraceId"),
            notificationTraceId = e.GetString("NotificationTraceId"),
            sku = e.GetString("Sku"),
            createdUtc = e.GetDateTime("CreatedUtc")
        });
    }
    catch (Azure.RequestFailedException ex) when (ex.Status == 404) { return Results.NotFound(); }
});

app.MapGet("/api/orders", async (TableClient table, CancellationToken ct) =>
{
    var list = new List<OrderSummary>();
    await foreach (var e in table.QueryAsync<TableEntity>(x => x.PartitionKey == "orders", maxPerPage: 100, cancellationToken: ct))
        list.Add(new OrderSummary(e.RowKey, e.GetString("CorrelationId"), e.GetString("TraceId"), e.GetString("Status"),
            e.GetString("PaymentStatus"), e.GetString("NotificationStatus"), e.GetString("Sku"), e.GetDateTime("CreatedUtc")));
    return Results.Ok(list.OrderByDescending(o => o.CreatedUtc).Take(25));
});

app.Run();

string Req(string key) => cfg[key] ?? throw new InvalidOperationException($"{key} is not configured.");

namespace CorrelationTracing.OrderApi
{
    public record OrderRequest(string? CustomerId, string Sku, decimal Amount);
    public record OrderPlaced(string OrderId, string CustomerId, string Sku, decimal Amount);
    public record OrderSummary(string OrderId, string? CorrelationId, string? TraceId, string? Status,
        string? PaymentStatus, string? NotificationStatus, string? Sku, DateTime? CreatedUtc);

    public static class Telemetry
    {
        public const string SourceName = "CorrelationTracing.OrderApi";
        public static readonly ActivitySource Source = new(SourceName);
    }
}
