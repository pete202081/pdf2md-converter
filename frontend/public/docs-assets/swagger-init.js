/* The Swagger bundle, the schema, and this bootstrap all come from this origin.
   No inline code, CDN or external fonts: nginx's site-wide CSP stays intact. */
window.ui = SwaggerUIBundle({
  url: '/openapi.json',
  dom_id: '#swagger-ui',
  deepLinking: true,
  validatorUrl: null, // No external schema validator calls.
  presets: [SwaggerUIBundle.presets.apis],
  layout: 'BaseLayout',
});
