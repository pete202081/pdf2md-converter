# Offline API documentation assets

The bundled assets are vendored from npm tarballs and served by nginx at `/docs-assets/`:

- `swagger-ui-dist` **5.33.1** (Apache-2.0): `swagger-ui.css` and `swagger-ui-bundle.js`
- `redoc` **2.5.4** (MIT): `bundles/redoc.standalone.js` (patched to serve the attribution icon locally)

Their respective package and bundled-third-party license notices are retained here.
To update, copy only these exact files from a pinned npm release, review upstream
security notices, update these versions, and verify `/docs`, `/redoc` and the CSP.
Never add inline scripts or remote scripts/styles to the docs HTML.

`redocly-logo.svg` is downloaded from `https://cdn.redoc.ly/redoc/logo-mini.svg`; the attribution and link are preserved, but the bundled script requests the icon at `/docs-assets/redocly-logo.svg` so it works offline under CSP.
