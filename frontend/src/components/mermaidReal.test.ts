import { expect, it } from 'vitest';
import mermaid from 'mermaid';

it('parses the Mermaid syntax emitted by the conversion pipeline', async () => {
  mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'neutral' });
  expect(await mermaid.parse('flowchart TD\nA[Start] --> B[Finish]')).toBeTruthy();
  expect(await mermaid.parse('sequenceDiagram\nAlice->>Bob: Hello')).toBeTruthy();
});
