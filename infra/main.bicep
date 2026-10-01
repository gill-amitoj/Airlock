// Azure deployment for the Workflow Orchestration Engine.
//
// Sized to stay inside free allowances:
// - One Container App holding the API, worker and Redis as sibling containers.
//   It scales to zero when idle, so it only uses the monthly free vCPU grant
//   while someone is using it. Max one replica, because Redis lives in-pod.
// - PostgreSQL Flexible Server on Burstable B1ms with 32 GB storage, the size
//   covered by the 12-month free offer.
// - Images pulled from GitHub Container Registry (public), not ACR.
// - The AI generator is disabled: there is no Ollama server in the cloud.

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Prefix for resource names')
param namePrefix string = 'airlock'

@description('Registry and namespace the images live under')
param imageRegistry string = 'ghcr.io/gill-amitoj'

@description('Image tag to deploy (git SHA from CI, or latest)')
param imageTag string = 'latest'

@description('PostgreSQL admin login')
param postgresAdmin string = 'airlockadmin'

@description('PostgreSQL admin password (URL-safe characters only)')
@secure()
param postgresPassword string

@description('Flask SECRET_KEY')
@secure()
param secretKey string

@description('Admin key required for write requests (X-API-Key header)')
@secure()
param apiKey string

var suffix = uniqueString(resourceGroup().id)
var dbName = 'workflow_engine'
var apiImage = '${imageRegistry}/airlock-api:${imageTag}'
var workerImage = '${imageRegistry}/airlock-worker:${imageTag}'

// ============================================
// LOGGING
// ============================================

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-logs-${suffix}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// ============================================
// DATABASE
// ============================================

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: '${namePrefix}-pg-${suffix}'
  location: location
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '16'
    administratorLogin: postgresAdmin
    administratorLoginPassword: postgresPassword
    storage: { storageSizeGB: 32 }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' }
  }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgres
  name: dbName
}

// Azure blocks extensions until they are allowlisted; the schema needs uuid-ossp.
resource pgExtensions 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2024-08-01' = {
  parent: postgres
  name: 'azure.extensions'
  properties: {
    value: 'UUID-OSSP'
    source: 'user-override'
  }
}

// 0.0.0.0 - 0.0.0.0 is Azure's special rule for "allow Azure services".
// Container Apps on the consumption plan have no fixed outbound IP to list instead.
resource pgFirewallAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: postgres
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

var databaseUrl = 'postgresql://${postgresAdmin}:${postgresPassword}@${postgres.properties.fullyQualifiedDomainName}:5432/${dbName}?sslmode=require'

// ============================================
// CONTAINER APPS
// ============================================

// Set explicitly: new environments otherwise default to Express mode, which
// supports neither sidecars (worker and Redis run beside the API) nor init
// containers (migrations). Only the Consumption profile is defined, so there
// is no dedicated, always-billed compute.
resource environment 'Microsoft.App/managedEnvironments@2026-07-01' = {
  name: '${namePrefix}-env-${suffix}'
  location: location
  properties: {
    environmentMode: 'WorkloadProfiles'
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

var sharedEnv = [
  { name: 'DATABASE_URL', secretRef: 'database-url' }
  { name: 'REDIS_URL', value: 'redis://localhost:6379/0' }
  { name: 'LLM_ENABLED', value: 'false' }
  { name: 'FLASK_ENV', value: 'production' }
  { name: 'FLASK_DEBUG', value: 'false' }
  { name: 'LOG_LEVEL', value: 'INFO' }
  // Dashboard is served same-origin, so no cross-origin access is allowed.
  { name: 'CORS_ORIGINS', value: '' }
  // Container Apps ingress is the single proxy hop in front of the app.
  { name: 'TRUSTED_PROXY_HOPS', value: '1' }
]

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-app'
  location: location
  dependsOn: [
    database
    pgExtensions
    pgFirewallAzure
  ]
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 5000
        transport: 'auto'
        // Plain HTTP is redirected to HTTPS.
        allowInsecure: false
      }
      secrets: [
        { name: 'database-url', value: databaseUrl }
        { name: 'secret-key', value: secretKey }
        { name: 'api-key', value: apiKey }
      ]
    }
    template: {
      // Runs before the app containers on every new revision; safe to repeat.
      initContainers: [
        {
          name: 'migrate'
          image: apiImage
          command: [ 'python', '-m', 'src.persistence.migrate' ]
          env: sharedEnv
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
      containers: [
        {
          name: 'api'
          image: apiImage
          env: concat(sharedEnv, [
            { name: 'SECRET_KEY', secretRef: 'secret-key' }
            { name: 'API_KEY', secretRef: 'api-key' }
          ])
          resources: { cpu: json('0.5'), memory: '1Gi' }
        }
        {
          name: 'worker'
          image: workerImage
          env: sharedEnv
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
        {
          // Queue only; PostgreSQL is the source of truth for execution state.
          name: 'redis'
          image: 'docker.io/library/redis:7-alpine'
          command: [ 'redis-server', '--save', '', '--appendonly', 'no' ]
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
        rules: [
          {
            name: 'http'
            http: { metadata: { concurrentRequests: '20' } }
          }
        ]
      }
    }
  }
}

output url string = 'https://${app.properties.configuration.ingress.fqdn}'
output postgresHost string = postgres.properties.fullyQualifiedDomainName
