using Azure.Data.Tables;
using Azure.Identity;
using Azure.Monitor.OpenTelemetry.Exporter;
using CorrelationTracing.Workers;
using Microsoft.Azure.Functions.Worker;
using Microsoft.Azure.Functions.Worker.OpenTelemetry;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using OpenTelemetry;
using OpenTelemetry.Logs;
using OpenTelemetry.Trace;

var host = new HostBuilder()
    .ConfigureFunctionsWorkerDefaults()
    .ConfigureServices((ctx, services) =>
    {
        var cfg = ctx.Configuration;
        var ratio = double.TryParse(cfg["Tracing:SamplingRatio"], out var r) ? r : 1.0;

        // OpenTelemetry mode for the isolated worker (host.json telemetryMode = OpenTelemetry).
        // ParentBased: a span whose parent was sampled is sampled, so a trace that started in the
        // API arrives here whole or not at all. The ratio only applies to traces that start here.
        services.AddOpenTelemetry()
            .UseFunctionsWorkerDefaults()
            .WithTracing(t => t
                .AddSource(Telemetry.SourceName)
                .AddHttpClientInstrumentation()
                .AddProcessor(new CorrelationTagProcessor())
                .SetSampler(new ParentBasedSampler(new TraceIdRatioBasedSampler(ratio))))
            .UseAzureMonitorExporter();

        services.Configure<OpenTelemetryLoggerOptions>(o => { o.IncludeScopes = true; o.ParseStateValues = true; });

        var credential = new DefaultAzureCredential();
        services.AddSingleton(new TableClient(new Uri(cfg["Storage:TableServiceUri"]!), "Orders", credential));
        services.AddTransient<CorrelationHeaderHandler>();
        services.AddHttpClient("payments", c => c.BaseAddress = new Uri(cfg["PaymentProvider:BaseUrl"]!))
            .AddHttpMessageHandler<CorrelationHeaderHandler>();
    })
    .Build();

host.Run();

namespace CorrelationTracing.Workers
{
    public static class Telemetry
    {
        public const string SourceName = "CorrelationTracing.Workers";
        public static readonly System.Diagnostics.ActivitySource Source = new(SourceName);
    }
}
