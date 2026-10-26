param storageAccountName string
param serviceBusName string
param apiPrincipalId string
param workersPrincipalId string

var roles = {
  storageBlobDataOwner: 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
  storageQueueDataContributor: '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
  storageTableDataContributor: '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
  serviceBusDataSender: '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
  serviceBusDataReceiver: '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = { name: storageAccountName }
resource serviceBus 'Microsoft.ServiceBus/namespaces@2024-01-01' existing = { name: serviceBusName }

// API: writes the Orders table, sends to the topic. Nothing else.
resource apiTable 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, apiPrincipalId, roles.storageTableDataContributor)
  scope: storage
  properties: { principalId: apiPrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.storageTableDataContributor) }
}

resource apiSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBus.id, apiPrincipalId, roles.serviceBusDataSender)
  scope: serviceBus
  properties: { principalId: apiPrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.serviceBusDataSender) }
}

// Workers: Functions host storage, the Orders table, receive from the topic. They never send.
resource workersBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, workersPrincipalId, roles.storageBlobDataOwner)
  scope: storage
  properties: { principalId: workersPrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.storageBlobDataOwner) }
}

resource workersQueue 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, workersPrincipalId, roles.storageQueueDataContributor)
  scope: storage
  properties: { principalId: workersPrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.storageQueueDataContributor) }
}

resource workersTable 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, workersPrincipalId, roles.storageTableDataContributor)
  scope: storage
  properties: { principalId: workersPrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.storageTableDataContributor) }
}

resource workersReceiver 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBus.id, workersPrincipalId, roles.serviceBusDataReceiver)
  scope: serviceBus
  properties: { principalId: workersPrincipalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.serviceBusDataReceiver) }
}
