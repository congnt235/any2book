import { describe, expect, it } from 'vitest';
import {
  buildWindowsInstallPlan,
  dependenciesForSources,
  resolveSourceNeeds,
} from '../src/dependencies.js';

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

  it('installs required dependencies with Winget without blocking on optional EPUBCheck', () => {
    const plan = buildWindowsInstallPlan(['uv', 'epubcheck', 'pandoc'], ['winget']);
    expect(plan.map((step) => step.dependency)).toEqual(['uv', 'epubcheck', 'pandoc']);
    expect(plan.filter((step) => 'command' in step).map((step) => step.dependency))
      .toEqual(['uv', 'pandoc']);
    expect(plan.find((step) => step.dependency === 'epubcheck'))
      .toEqual(expect.objectContaining({ manual: expect.stringContaining('manually') }));
  });

  it('uses Chocolatey for EPUBCheck when both Windows managers are available', () => {
    const plan = buildWindowsInstallPlan(['uv', 'epubcheck'], ['winget', 'choco']);
    expect(plan).toEqual([
      expect.objectContaining({ dependency: 'uv', command: 'winget' }),
      expect.objectContaining({ dependency: 'epubcheck', command: 'choco' }),
    ]);
  });

  it('returns manual EPUBCheck guidance without a Windows package manager', () => {
    expect(buildWindowsInstallPlan(['epubcheck', 'calibre'], [])).toEqual([
      expect.objectContaining({ dependency: 'epubcheck', manual: expect.any(String) }),
      expect.objectContaining({ dependency: 'calibre', manual: expect.stringContaining('Calibre') }),
    ]);
    expect(() => buildWindowsInstallPlan(['uv'], [])).toThrow('required dependency: uv');
  });
});
