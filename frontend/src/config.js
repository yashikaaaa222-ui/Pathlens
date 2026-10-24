// Constants for the interface only. The scoring parameters (weights, chunk length, buffers, half-lives) live on
// the server and are published at GET /api/methodology, so the browser never keeps a second copy that could drift.
export const CONFIG = {
  center: [51.505, -0.09], // placeholder map centre: set this to your prototype city
  zoom: 14,
  routeColors: ['#0F7C8A', '#7A4FA8', '#B86E00'], // identify routes; they carry no good/bad meaning
};
