`server.js.DEPRECATED` is the old second backend (Node/Express, port 3000). It is NOT part of the deployment any more:
it served the whole project folder (source, logs) and accepted unauthenticated telemetry POSTs. The Python gateway is the single source of truth.
Kept only for reference; delete freely.
