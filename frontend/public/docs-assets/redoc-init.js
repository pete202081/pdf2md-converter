/* ReDoc's search worker uses blob: URLs, which the site-wide CSP disallows.
   Disabling search preserves the policy without weakening other pages. */
Redoc.init('/openapi.json', {
  disableSearch: true,
  theme: { typography: { fontFamily: 'system-ui, sans-serif' } },
}, document.getElementById('redoc-container'));
