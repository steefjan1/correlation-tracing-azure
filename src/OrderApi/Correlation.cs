using System.Diagnostics;
using OpenTelemetry;

namespace CorrelationTracing.OrderApi;

/// <summary>
/// The business correlation id. Distinct from the W3C trace id on purpose:
///  - the trace id is minted by the platform per request and read by engineers in a waterfall;
///  - the correlation id is owned by the business, lives for the whole workflow and is what the
///    service desk types into a search box.
/// This file is duplicated in src/Workers on purpose, so each deployable stays self-contained.
/// </summary>
public static class Correlation
{
    public const string Header = "X-Correlation-ID";
    public const string BaggageKey = "correlation.id";      // W3C baggage, crosses HTTP hops automatically
    public const string TagKey = "correlation.id";          // span attribute, queryable in Application Insights
    public const string MessageProperty = "CorrelationId";  // Service Bus application property, set by hand

    public static string NewId() => $"ord-{DateTime.UtcNow:yyyyMMdd}-{Guid.NewGuid().ToString("N")[..6]}";

    public static string? Current => Baggage.GetBaggage(BaggageKey);

    /// <summary>Puts the id in baggage (so outgoing HTTP calls carry it) and on the current span.</summary>
    public static void Set(string id)
    {
        Baggage.SetBaggage(BaggageKey, id);
        Activity.Current?.SetTag(TagKey, id);
    }
}

/// <summary>Copies the correlation id from baggage onto every span that starts while it is set,
/// so a single Kusto filter on customDimensions["correlation.id"] finds every span of a workflow.</summary>
public sealed class CorrelationTagProcessor : BaseProcessor<Activity>
{
    public override void OnStart(Activity activity)
    {
        var id = Baggage.GetBaggage(Correlation.BaggageKey);
        if (!string.IsNullOrEmpty(id)) activity.SetTag(Correlation.TagKey, id);
    }
}

/// <summary>Adds X-Correlation-ID to outgoing HTTP calls. traceparent is added by the HttpClient
/// instrumentation; baggage travels in the W3C "baggage" header, also automatically. This header
/// exists for the receivers that know nothing about OpenTelemetry.</summary>
public sealed class CorrelationHeaderHandler : DelegatingHandler
{
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
    {
        var id = Correlation.Current;
        if (!string.IsNullOrEmpty(id) && !request.Headers.Contains(Correlation.Header))
            request.Headers.TryAddWithoutValidation(Correlation.Header, id);
        return base.SendAsync(request, ct);
    }
}
