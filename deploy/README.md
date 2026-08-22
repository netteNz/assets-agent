# Azure Container Apps deployment

This template creates exactly one Consumption environment and one externally accessible Container App. Logs are disabled; `minReplicas` is `0` and `maxReplicas` is `1`.

The image must be pullable by Container Apps. The lowest-resource option is making this personal GHCR package public. Do not place a GitHub PAT in the template just to pull a private image.

```bash
az deployment group create \
  --resource-group <resource-group> \
  --template-file deploy/containerapp.bicep \
  --parameters environmentName=<environment-name> image=ghcr.io/<owner>/coinbase-portfolio-api:<tag> \
  --parameters portfolioApiKey="$PORTFOLIO_API_KEY" coinbaseApiKeyId="$COINBASE_API_KEY_ID" coinbaseApiKeySecret="$COINBASE_API_KEY_SECRET"
```

Retrieve the deployed URL with `az containerapp show --name coinbase-portfolio-api --resource-group <resource-group> --query properties.configuration.ingress.fqdn -o tsv`.

