param name string
param location string
param publisherEmail string
param publisherName string
param appInsightsId string
@secure()
param appInsightsInstrumentationKey string
param backendHostName string
param tags object = {}

// Consumption tier: pay per call, no fixed fee, activates in a few minutes. Good enough for a
// gateway whose only job here is to own the business correlation id at the edge.
resource apim 'Microsoft.ApiManagement/service@2023-09-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'Consumption', capacity: 0 }
  identity: { type: 'SystemAssigned' }
  properties: {
    publisherEmail: publisherEmail
    publisherName: publisherName
  }

  resource logger 'loggers' = {
    name: 'appinsights'
    properties: {
      loggerType: 'applicationInsights'
      resourceId: appInsightsId
      credentials: { instrumentationKey: appInsightsInstrumentationKey }
      isBuffered: true
    }
  }

  // W3C correlation protocol: the gateway span becomes the root of the trace and the
  // traceparent it forwards is the one the API continues.
  resource diagnostics 'diagnostics' = {
    name: 'applicationinsights'
    properties: {
      loggerId: logger.id
      alwaysLog: 'allErrors'
      httpCorrelationProtocol: 'W3C'
      verbosity: 'information'
      logClientIp: false
      sampling: { samplingType: 'fixed', percentage: 100 }
      frontend: {
        request: { headers: [ 'X-Correlation-ID', 'traceparent' ], body: { bytes: 0 } }
        response: { headers: [ 'X-Correlation-ID' ], body: { bytes: 0 } }
      }
      backend: {
        request: { headers: [ 'X-Correlation-ID', 'traceparent' ], body: { bytes: 0 } }
        response: { headers: [ 'X-Correlation-ID' ], body: { bytes: 0 } }
      }
    }
  }

  resource ordersApi 'apis' = {
    name: 'orders'
    properties: {
      displayName: 'Orders'
      path: 'orders'
      protocols: [ 'https' ]
      serviceUrl: 'https://${backendHostName}'
      subscriptionRequired: false
    }

    resource anyGet 'operations' = {
      name: 'any-get'
      properties: { displayName: 'GET anything', method: 'GET', urlTemplate: '/*' }
    }
    resource anyPost 'operations' = {
      name: 'any-post'
      properties: { displayName: 'POST anything', method: 'POST', urlTemplate: '/*' }
    }

    // Accept the caller's X-Correlation-ID when present, mint one when absent, and always echo
    // it back. traceparent is left alone: the W3C protocol setting above handles it.
    resource policy 'policies' = {
      name: 'policy'
      properties: {
        format: 'rawxml'
        value: '''
<policies>
  <inbound>
    <base />
    <set-header name="X-Correlation-ID" exists-action="skip">
      <value>@("gw-" + Guid.NewGuid().ToString("N").Substring(0, 12))</value>
    </set-header>
    <set-variable name="correlationId" value="@(context.Request.Headers.GetValueOrDefault("X-Correlation-ID", ""))" />
    <set-header name="X-Gateway-Request-Id" exists-action="override">
      <value>@(context.RequestId.ToString())</value>
    </set-header>
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
    <set-header name="X-Correlation-ID" exists-action="override">
      <value>@((string)context.Variables["correlationId"])</value>
    </set-header>
  </outbound>
  <on-error>
    <base />
    <set-header name="X-Correlation-ID" exists-action="override">
      <value>@((string)context.Variables.GetValueOrDefault("correlationId", ""))</value>
    </set-header>
  </on-error>
</policies>
'''
      }
    }
  }
}

output gatewayUrl string = apim.properties.gatewayUrl
output principalId string = apim.identity.principalId
