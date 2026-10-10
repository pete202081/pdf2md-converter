import { expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(process.cwd(), 'public');
const nginx = readFileSync(resolve(process.cwd(), 'nginx.conf'), 'utf8');

for (const [route, page, bundle, boot] of [
  ['/docs', 'docs.html', 'swagger-ui-bundle.js', 'swagger-init.js'],
  ['/redoc', 'redoc.html', 'redoc.standalone.js', 'redoc-init.js'],
] as const) {
  it(`${route} has only same-origin scripts/styles and no inline code`, () => {
    const html = readFileSync(resolve(root, page), 'utf8');
    expect(html).not.toMatch(/https?:\/\//i);
    expect(html).not.toMatch(/<script(?![^>]*\bsrc=)[^>]*>/i);
    expect(html).toContain(`/docs-assets/${bundle}`);
    expect(html).toContain(`/docs-assets/${boot}`);
    const links = [...html.matchAll(/\b(?:href|src)="([^"]+)"/g)];
    for (const [, url] of links) {
      expect(url).toMatch(/^\//);
      expect(existsSync(resolve(root, url.slice(1)))).toBe(true);
    }
    expect(readFileSync(resolve(root, 'docs-assets', boot), 'utf8')).toContain("'/openapi.json'");
    expect(nginx).toMatch(new RegExp(`location = ${route}\\s+\\{ rewrite \\^ /${page} last; \\}`));
  });
}

it('preserves the strict CSP, the live schema proxy, and disables the ReDoc blob search worker', () => {
  expect(nginx).toContain("script-src 'self';");
  expect(nginx).not.toContain("script-src 'self' 'unsafe-inline'");
  expect(nginx).toContain('location = /openapi.json { proxy_pass http://api:8000/openapi.json;');
  expect(readFileSync(resolve(root, 'docs-assets/redoc-init.js'), 'utf8')).toContain('disableSearch: true');
  expect(readFileSync(resolve(root, 'docs-assets/redoc.standalone.js'), 'utf8')).toContain('/docs-assets/redocly-logo.svg');
  expect(existsSync(resolve(root, 'docs-assets/redocly-logo.svg'))).toBe(true);
  expect(readFileSync(resolve(root, 'docs-assets/swagger-init.js'), 'utf8')).toContain('validatorUrl: null');
});
