// @vitest-environment jsdom

import type { PropsWithChildren } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  getActiveTokenQuickDays,
  getLastDaysRange,
  normalizeCustomTokenRange,
  resolveTokenRange,
} from '../-lib/format'
import { normalizeTokenSeries } from '../-lib/normalize'
import type { HomeT } from '../-types/dashboard'
import { TokenTrendChart } from './token-trend-chart'

vi.mock('recharts', () => ({
  Area: ({
    dataKey,
    fill,
    stroke,
  }: {
    dataKey?: string
    fill?: string
    stroke?: string
  }) => (
    <g
      data-fill={fill}
      data-key={dataKey}
      data-stroke={stroke}
      data-testid={`token-trend-area-${dataKey}`}
    />
  ),
  AreaChart: ({ children }: PropsWithChildren) => <svg>{children}</svg>,
  CartesianGrid: () => null,
  ResponsiveContainer: ({ children }: PropsWithChildren) => <>{children}</>,
  Tooltip: () => null,
  XAxis: () => null,
  YAxis: ({ width }: { width?: number | 'auto' }) => (
    <g data-testid="token-trend-y-axis" data-width={width} />
  ),
}))

afterEach(cleanup)

describe('TokenTrendChart', () => {
  it('lets the Y-axis fit million-scale labels without clipping and renders all four series', () => {
    render(
      <TokenTrendChart
        items={[
          {
            date: '2026-08-18',
            embedding_input: 1_411_688,
            rerank_input: 52_400,
            total: 26_998_450,
            vlm_input: 21_445_905,
            vlm_output: 4_088_457,
          },
        ]}
        t={((key: string) => key) as HomeT}
      />,
    )

    expect(
      screen.getByTestId('token-trend-y-axis').getAttribute('data-width'),
    ).toBe('auto')
    expect(
      screen
        .getByTestId('token-trend-area-vlm_input')
        .getAttribute('data-stroke'),
    ).toBe('var(--home-token-input)')
    expect(
      screen
        .getByTestId('token-trend-area-vlm_output')
        .getAttribute('data-stroke'),
    ).toBe('var(--home-token-output)')
    expect(
      screen
        .getByTestId('token-trend-area-embedding_input')
        .getAttribute('data-stroke'),
    ).toBe('var(--home-token-embedding)')
    expect(
      screen
        .getByTestId('token-trend-area-rerank_input')
        .getAttribute('data-stroke'),
    ).toBe('var(--home-token-rerank)')
    expect(
      screen
        .getByTestId('token-trend-area-rerank_input')
        .getAttribute('data-fill'),
    ).toBe('url(#tokenTrendRerank)')
    expect(screen.getByText('todayTokens.rerankInput')).toBeTruthy()
  })

  it('normalizes rerank_input and token date ranges', () => {
    const normalized = normalizeTokenSeries([
      {
        date: '2026-10-14',
        embedding_input: 200,
        rerank_input: 50,
        vlm_input: 1000,
        vlm_output: 300,
      },
    ])
    expect(normalized).toEqual([
      {
        date: '2026-10-14',
        embedding_input: 200,
        rerank_input: 50,
        total: 1550,
        vlm_input: 1000,
        vlm_output: 300,
      },
    ])

    const fixedNow = new Date('2026-10-14T12:00:00Z')
    const defaultRange = resolveTokenRange(null, fixedNow, 'UTC')
    expect(defaultRange).toEqual({
      endDate: '2026-10-14',
      startDate: '2026-10-01',
    })
    expect(getActiveTokenQuickDays(defaultRange, fixedNow, 'UTC')).toBe(14)

    const range7 = getLastDaysRange(7, 'UTC', fixedNow)
    expect(getActiveTokenQuickDays(range7, fixedNow, 'UTC')).toBe(7)

    expect(normalizeCustomTokenRange('2026-10-10', '2026-10-02')).toEqual({
      endDate: '2026-10-10',
      startDate: '2026-10-02',
    })
    expect(normalizeCustomTokenRange('', '2026-10-10')).toBeNull()
  })
})
