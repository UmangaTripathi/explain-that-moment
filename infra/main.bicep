// Explain That Moment: all Azure resources for the project.
// Deploy at resource-group scope: see scripts/bootstrap.sh

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Short prefix used in resource names.')
param prefix string = 'etm'

@description('Container image for the backend. Defaults to a placeholder until CI pushes the real image.')
param backendImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Port the backend container listens on.')
param backendPort int = 80

@description('Primary reasoning model (Analyst, Verifier).')
param mainModel object = {
  name: 'gpt-5-mini'
  version: '2025-08-07'
  capacity: 200 // thousands of tokens per minute
}

@description('Fast/cheap model (Narrator, Recap, translation).')
param miniModel object = {
  name: 'gpt-4.1-mini'
  version: '2025-04-14'
  capacity: 150
}

@description('Monthly budget in USD for the resource group.')
param budgetAmount int = 25

@description('Email that receives budget alerts.')
param budgetEmail string

@description('Budget start date (first day of a month, yyyy-MM-01).')
param budgetStartDate string = '2026-10-01'

var suffix = uniqueString(resourceGroup().id)
var tags = { project: 'explain-that-moment' }

// ---------------------------------------------------------------------------
// Identity used by the backend to reach Foundry and pull from ACR (no keys).
// ---------------------------------------------------------------------------
resource backendIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-backend-id'
  location: location
  tags: tags
}

// ---------------------------------------------------------------------------
// Microsoft Foundry: AI Services account, project, and model deployments.
// ---------------------------------------------------------------------------
resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: '${prefix}-foundry-${suffix}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: '${prefix}-foundry-${suffix}'
    allowProjectManagement: true
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
  }
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundry
  name: '${prefix}-project'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'Explain That Moment'
    description: 'Agents for explainable match intelligence'
  }
}

resource mainDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: mainModel.name
  dependsOn: [ foundryProject ] // one operation at a time on the account
  sku: { name: 'GlobalStandard', capacity: mainModel.capacity }
  properties: {
    model: { format: 'OpenAI', name: mainModel.name, version: mainModel.version }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

// Deployments on one account must be created one at a time.
resource miniDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: miniModel.name
  dependsOn: [ mainDeployment ]
  sku: { name: 'GlobalStandard', capacity: miniModel.capacity }
  properties: {
    model: { format: 'OpenAI', name: miniModel.name, version: miniModel.version }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

// ---------------------------------------------------------------------------
// Container hosting: Log Analytics, ACR, Container Apps environment + app.
// ---------------------------------------------------------------------------
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: '${prefix}acr${suffix}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource containerEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource backend 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-backend'
  location: location
  tags: tags
  dependsOn: [ acrPull ]
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${backendIdentity.id}': {} }
  }
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: backendPort
        transport: 'auto'
        corsPolicy: {
          allowedOrigins: [ 'https://${frontend.properties.defaultHostname}', 'http://localhost:5173' ]
          allowedMethods: [ 'GET', 'POST', 'OPTIONS' ]
          allowedHeaders: [ '*' ]
        }
      }
      registries: [
        { server: acr.properties.loginServer, identity: backendIdentity.id }
      ]
    }
    template: {
      containers: [
        {
          name: 'backend'
          image: backendImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'AZURE_CLIENT_ID', value: backendIdentity.properties.clientId }
            { name: 'AZURE_OPENAI_ENDPOINT', value: 'https://${foundry.properties.customSubDomainName}.openai.azure.com/' }
            { name: 'FOUNDRY_PROJECT_ENDPOINT', value: 'https://${foundry.properties.customSubDomainName}.services.ai.azure.com/api/projects/${foundryProject.name}' }
            { name: 'MAIN_MODEL_DEPLOYMENT', value: mainDeployment.name }
            { name: 'MINI_MODEL_DEPLOYMENT', value: miniDeployment.name }
            { name: 'ALLOWED_ORIGINS', value: 'https://${frontend.properties.defaultHostname}' }
          ]
        }
      ]
      scale: { minReplicas: 0, maxReplicas: 2 }
    }
  }
}

// ---------------------------------------------------------------------------
// Frontend hosting.
// ---------------------------------------------------------------------------
resource frontend 'Microsoft.Web/staticSites@2023-12-01' = {
  name: '${prefix}-frontend'
  location: location
  tags: tags
  sku: { name: 'Free', tier: 'Free' }
  properties: {}
}

// ---------------------------------------------------------------------------
// Role assignments for the backend identity.
// ---------------------------------------------------------------------------
var roles = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  openAiUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, backendIdentity.id, roles.acrPull)
  properties: {
    principalId: backendIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
  }
}

resource openAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundry
  name: guid(foundry.id, backendIdentity.id, roles.openAiUser)
  properties: {
    principalId: backendIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.openAiUser)
  }
}

// ---------------------------------------------------------------------------
// Cost guard rail: email subscription owners at 80% and 100% of budget.
// ---------------------------------------------------------------------------
resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: '${prefix}-budget'
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: { startDate: budgetStartDate }
    notifications: {
      actual80: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 80
        contactEmails: [ budgetEmail ]
        contactRoles: [ 'Owner' ]
      }
      forecast100: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 100
        thresholdType: 'Forecasted'
        contactEmails: [ budgetEmail ]
        contactRoles: [ 'Owner' ]
      }
    }
  }
}

output foundryName string = foundry.name
output foundryProjectName string = foundryProject.name
output openAiEndpoint string = 'https://${foundry.properties.customSubDomainName}.openai.azure.com/'
output foundryProjectEndpoint string = 'https://${foundry.properties.customSubDomainName}.services.ai.azure.com/api/projects/${foundryProject.name}'
output mainModelDeployment string = mainDeployment.name
output miniModelDeployment string = miniDeployment.name
output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output backendAppName string = backend.name
output backendUrl string = 'https://${backend.properties.configuration.ingress.fqdn}'
output frontendName string = frontend.name
output frontendUrl string = 'https://${frontend.properties.defaultHostname}'
