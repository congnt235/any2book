import { describe, expect, it } from 'vitest';
import { dependenciesForSources, resolveSourceNeeds } from '../src/dependencies.js';

describe('need-based dependency selection', () => {
  it('maps PDF needs without exposing technology choices', () => {
    expect(dependenciesForSources(['pdf'])).toEqual(['uv', 'epubcheck', 'pandoc']);
  });

  it('adds Calibre only when MOBI support is requested', () => {
    expect(dependenciesForSources(['epub'])).not.toContain('calibre');
    expect(dependenciesForSources(['mobi'])).toContain('calibre');
  });

  it('validates source names', () => {
    expect(() => resolveSourceNeeds(['video'], false)).toThrow('Unknown source types');
  });
});
