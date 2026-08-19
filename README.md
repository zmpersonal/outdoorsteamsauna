# OutdoorSteamSauna.com

GitHub Pages-ready U.S. Outdoor Sauna Climate Index and planning toolkit.

## First deployment
1. Upload all files, including `.github`.
2. Settings → Pages → Source: GitHub Actions.
3. Set custom domain to `outdoorsteamsauna.com`.
4. Add repository secret `EIA_API_KEY` (free EIA Open Data key).
5. Actions → **Update outdoor sauna climate index and deploy** → Run workflow.

The updater fetches NOAA/NCEI 1991–2020 monthly climate normals around each indexed metro, recent daily context when available, and the latest EIA residential electricity price by state. It rebuilds and deploys the site in the same workflow.

The site contains exactly one link to InHouseWellness.com, on `/recommended-retailer/`.
