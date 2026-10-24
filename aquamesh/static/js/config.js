/* AquaMesh UI configuration.
   backend: 'auto'  -> use a real server if /api/snapshot answers, else fall back to the built-in simulator
            'live'  -> always talk to the real server (baseUrl + /api/...)
            'mock'  -> always use the built-in simulator (works from file://)            */
window.AQUAMESH_CONFIG = {
  backend: 'auto',
  baseUrl: '',            // e.g. 'http://192.168.1.50:8000' for a Pi / gateway on the LAN
  token: '',              // sent as X-Token on POSTs (or use ?token=... in the page URL)
  mockSpeed: 4,           // simulated seconds per real second
  mockStartScenario: 'heavy_rain'
};
