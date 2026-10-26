targetScope = 'resourceGroup'

@description('azd environment name; the first 12 characters go into every resource name.')
@minLength(3)
param environmentName string

param location string = resourceGroup().location

@description('Head sampling ratio for traces that start in the API or the workers. 1.0 keeps everything; the measurement flips it to 0.1.')
param samplingRatio string = '1.0'

@description('API Management requires a publisher email; any syntactically valid address will do for the Consumption tier.')
param apimPublisherEmail string = 'admin@example.com'
param apimPublisherName string = 'Cloud Perspectives'

var suffix = toLower(uniqueString(subscription().id, resourceGroup().id, environmentName))
var prefix = take(replace(toLower(environmentName), '-', ''), 12)

var names = {
  law: 'log-${prefix}-${suffix}'
  appInsights: 'appi-${prefix}-${suffix}'
  storage: take('st${replace(prefix, '-', '')}${suffix}', 24)
  serviceBus: 'sb-${prefix}-${suffix}'
  appPlan: 'asp-${prefix}-${suffix}'
  api: 'app-${prefix}-orders-${suffix}'
  funcPlan: 'fcp-${prefix}-${suffix}'
  workers: 'func-${prefix}-workers-${suffix}'
  workersIdentity: 'id-${prefix}-workers-${suffix}'
  apim: 'apim-${prefix}-${suffix}'
}

var tags = { 'azd-env-name': environmentName, project: 'correlation-tracing-azure' }

// ---------------------------------------------------------------- monitoring: one workspace-based Application Insights for every hop
resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: names.law
  location: location
  tags: tags
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: names.appInsights
  location: location
  tags: tags
  kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: law.id, IngestionMode: 'LogAnalytics' }
}

// ---------------------------------------------------------------- storage: Orders table + function deployment package
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: names.storage
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowSharedKeyAccess: false
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
  resource blobs 'blobServices' = {
    name: 'default'
    resource deployments 'containers' = { name: 'deployments' }
  }
  resource tables 'tableServices' = {
    name: 'default'
    resource orders 'tables' = { name: 'Orders' }
  }
}

// ---------------------------------------------------------------- service bus: one topic, two subscriptions (two branches of one trace)
resource serviceBus 'Microsoft.ServiceBus/namespaces@2024-01-01' = {
  name: names.serviceBus
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Standard' }
  properties: { disableLocalAuth: true, minimumTlsVersion: '1.2' }

  resource orders 'topics' = {
    name: 'orders'
    properties: { defaultMessageTimeToLive: 'P1D', requiresDuplicateDetection: true, duplicateDetectionHistoryTimeWindow: 'PT10M' }

    resource payment 'subscriptions' = {
      name: 'payment'
      properties: { maxDeliveryCount: 5, lockDuration: 'PT1M', deadLetteringOnMessageExpiration: true }
    }
    resource notification 'subscriptions' = {
      name: 'notification'
      properties: { maxDeliveryCount: 5, lockDuration: 'PT1M', deadLetteringOnMessageExpiration: true }
    }
  }
}

// ---------------------------------------------------------------- workers identity (user-assigned: clientId known at deploy time)
resource workersIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: names.workersIdentity
  location: location
  tags: tags
}

// ---------------------------------------------------------------- order api on app service
resource appPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: names.appPlan
  location: location
  tags: tags
  kind: 'linux'
  sku: { name: 'B1', tier: 'Basic' }
  properties: { reserved: true }
}

var workersBaseUrl = 'https://${names.workers}.azurewebsites.net/'

resource api 'Microsoft.Web/sites@2023-12-01' = {
  name: names.api
  location: location
  tags: union(tags, { 'azd-service-name': 'api' })
  kind: 'app,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: appPlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'DOTNETCORE|8.0'
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      healthCheckPath: '/health'
      appSettings: [
        // No ApplicationInsightsAgent_EXTENSION_VERSION here on purpose: the app ships the
        // OpenTelemetry distro, and the codeless agent would double-instrument it.
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
        { name: 'ServiceBus__fullyQualifiedNamespace', value: '${serviceBus.name}.servicebus.windows.net' }
        { name: 'Storage__TableServiceUri', value: storage.properties.primaryEndpoints.table }
        { name: 'PaymentProvider__BaseUrl', value: workersBaseUrl }
        { name: 'Tracing__SamplingRatio', value: samplingRatio }
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'false' }
      ]
    }
  }
}

// ---------------------------------------------------------------- workers on flex consumption
resource funcPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: names.funcPlan
  location: location
  tags: tags
  kind: 'functionapp'
  sku: { tier: 'FlexConsumption', name: 'FC1' }
  properties: { reserved: true }
}

resource workers 'Microsoft.Web/sites@2023-12-01' = {
  name: names.workers
  location: location
  tags: union(tags, { 'azd-service-name': 'workers' })
  kind: 'functionapp,linux'
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${workersIdentity.id}': {} } }
  properties: {
    serverFarmId: funcPlan.id
    httpsOnly: true
    keyVaultReferenceIdentity: workersIdentity.id
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}deployments'
          authentication: { type: 'UserAssignedIdentity', userAssignedIdentityResourceId: workersIdentity.id }
        }
      }
      scaleAndConcurrency: { maximumInstanceCount: 40, instanceMemoryMB: 2048 }
      runtime: { name: 'dotnet-isolated', version: '8.0' }
    }
    siteConfig: {
      minTlsVersion: '1.2'
      appSettings: [
        { name: 'AZURE_CLIENT_ID', value: workersIdentity.properties.clientId }
        { name: 'AzureWebJobsStorage__accountName', value: storage.name }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'AzureWebJobsStorage__clientId', value: workersIdentity.properties.clientId }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
        { name: 'ServiceBus__fullyQualifiedNamespace', value: '${serviceBus.name}.servicebus.windows.net' }
        { name: 'ServiceBus__credential', value: 'managedidentity' }
        { name: 'ServiceBus__clientId', value: workersIdentity.properties.clientId }
        { name: 'Storage__TableServiceUri', value: storage.properties.primaryEndpoints.table }
        { name: 'PaymentProvider__BaseUrl', value: workersBaseUrl }
        { name: 'Tracing__SamplingRatio', value: samplingRatio }
      ]
    }
  }
}

// ---------------------------------------------------------------- api management (consumption): the gateway that mints the business id
module apim 'modules/apim.bicep' = {
  name: 'apim'
  params: {
    name: names.apim
    location: location
    publisherEmail: apimPublisherEmail
    publisherName: apimPublisherName
    appInsightsId: appInsights.id
    appInsightsInstrumentationKey: appInsights.properties.InstrumentationKey
    backendHostName: '${names.api}.azurewebsites.net'
    tags: tags
  }
}

// ---------------------------------------------------------------- role assignments
module roles 'modules/roles.bicep' = {
  name: 'roles'
  params: {
    storageAccountName: storage.name
    serviceBusName: serviceBus.name
    apiPrincipalId: api.identity.principalId
    workersPrincipalId: workersIdentity.properties.principalId
  }
}

output AZURE_LOCATION string = location
output GATEWAY_URL string = '${apim.outputs.gatewayUrl}/orders'
output API_NAME string = api.name
output API_HOSTNAME string = api.properties.defaultHostName
output WORKERS_NAME string = workers.name
output WORKERS_HOSTNAME string = workers.properties.defaultHostName
output SERVICE_BUS_NAMESPACE string = serviceBus.name
output STORAGE_ACCOUNT string = storage.name
output APPINSIGHTS_NAME string = appInsights.name
output LOG_ANALYTICS_WORKSPACE_ID string = law.properties.customerId
output APIM_NAME string = names.apim
