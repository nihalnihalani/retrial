// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { DegradeBanner } from './DegradeBanner';
import type { BoardState, PreflightCheck } from '../types';

afterEach(cleanup);

const pf = (ok: boolean, checks: PreflightCheck[]): BoardState['preflight'] => ({
  ok,
  liveChecked: false,
  checks,
});

describe('DegradeBanner', () => {
  it('renders the RED degraded alert with the reason', () => {
    render(<DegradeBanner poolDegraded={{ reason: 'fork VM missing' }} preflight={null} />);
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('FORK POOL DEGRADED');
    expect(alert).toHaveTextContent('fork VM missing');
  });

  it('renders the preflight-failed alert naming the first failing check', () => {
    render(
      <DegradeBanner
        poolDegraded={null}
        preflight={pf(false, [{ name: 'daytona_api_key', status: 'fail', detail: 'missing' }])}
      />,
    );
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('PREFLIGHT FAILED');
    expect(alert).toHaveTextContent('daytona_api_key');
    expect(alert).toHaveTextContent('retrial doctor');
  });

  it('renders a slim amber warn strip when preflight ok but has warns', () => {
    render(
      <DegradeBanner
        poolDegraded={null}
        preflight={pf(true, [{ name: 'fork_region', status: 'warn', detail: 'not us-east-1' }])}
      />,
    );
    // warn variant uses role="status", not the loud "alert"
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('status')).toHaveTextContent('fork_region');
  });

  it('renders nothing when healthy (no degrade, no preflight)', () => {
    const { container } = render(<DegradeBanner poolDegraded={null} preflight={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when preflight ok with only passing checks', () => {
    const { container } = render(
      <DegradeBanner
        poolDegraded={null}
        preflight={pf(true, [{ name: 'daytona_api_key', status: 'pass', detail: 'present' }])}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('degrade wins over a failed preflight (loudest fact first)', () => {
    render(
      <DegradeBanner
        poolDegraded={{ reason: 'r' }}
        preflight={pf(false, [{ name: 'daytona_api_key', status: 'fail', detail: 'm' }])}
      />,
    );
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('FORK POOL DEGRADED');
    expect(alert).not.toHaveTextContent('PREFLIGHT FAILED');
  });
});
