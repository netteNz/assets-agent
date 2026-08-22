@description('Azure region for the Container Apps environment and app.')
param location string = resourceGroup().location
param environmentName string
param appName string = 'coinbase-portfolio-api'
@description('A public GHCR image reference. Use a private registry only after adding explicit pull credentials.')
param image string
@secure()
param portfolioApiKey string
@secure()
param coinbaseApiKeyId string
@secure()
param coinbaseApiKeySecret string

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {
    appLogsConfiguration: { destination: 'none' }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        allowInsecure: false
      }
      secrets: [
        { name: 'portfolio-api-key', value: portfolioApiKey }
        { name: 'coinbase-api-key-id', value: coinbaseApiKeyId }
        { name: 'coinbase-api-key-secret', value: coinbaseApiKeySecret }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: image
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: [
            { name: 'PORTFOLIO_API_KEY', secretRef: 'portfolio-api-key' }
            { name: 'COINBASE_API_KEY_ID', secretRef: 'coinbase-api-key-id' }
            { name: 'COINBASE_API_KEY_SECRET', secretRef: 'coinbase-api-key-secret' }
            { name: 'COINBASE_API_BASE_URL', value: 'https://api.coinbase.com' }
          ]
        }
      ]
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
}

output appFqdn string = app.properties.configuration.ingress.fqdn
output appUrl string = 'https://${app.properties.configuration.ingress.fqdn}'

