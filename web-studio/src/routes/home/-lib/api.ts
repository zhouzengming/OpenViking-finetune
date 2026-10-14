import {
  getConsoleContextCommits,
  getConsoleDashboardSummary,
  getConsoleTokens,
  getOvResult,
} from '#/lib/ov-client'

import { COMMIT_SERIES_DAYS } from '../-constants/dashboard'
import type {
  ConsoleSeriesQuery,
  ConsoleContextCommitsResult,
  ConsoleDashboardSummaryResult,
  ConsoleTokenSeriesResult,
} from '@ov-server/api/v1/console'
import type { TokenDateRange, TokenRangeOverride } from '../-types/dashboard'
import {
  asRecord,
  asString,
  getLastDaysRange,
  getViewerTimezone,
  resolveTokenRange,
} from './format'

function toTokenRangeOverride(value: unknown): TokenRangeOverride {
  const record = asRecord(value)
  const startDate = asString(record.startDate)
  const endDate = asString(record.endDate)
  if (!startDate || !endDate) return null
  return { endDate, startDate } satisfies TokenDateRange
}

export function fetchConsoleDashboardSummary(): Promise<ConsoleDashboardSummaryResult> {
  return getOvResult<ConsoleDashboardSummaryResult>(
    getConsoleDashboardSummary({ query: { timezone: getViewerTimezone() } }),
  )
}

export function fetchConsoleTokenSeries(
  rangeOverride?: TokenRangeOverride | unknown,
): Promise<ConsoleTokenSeriesResult> {
  const tz = getViewerTimezone()
  const range = resolveTokenRange(
    toTokenRangeOverride(rangeOverride),
    new Date(),
    tz,
  )
  const query: ConsoleSeriesQuery = {
    bucket: 'day',
    end_date: range.endDate,
    start_date: range.startDate,
    timezone: tz,
  }
  return getOvResult<ConsoleTokenSeriesResult>(getConsoleTokens({ query }))
}

export function fetchConsoleContextCommits(): Promise<ConsoleContextCommitsResult> {
  const tz = getViewerTimezone()
  const range = getLastDaysRange(COMMIT_SERIES_DAYS, tz)
  const query: ConsoleSeriesQuery = {
    bucket: '4h',
    end_date: range.endDate,
    start_date: range.startDate,
    timezone: tz,
  }
  return getOvResult<ConsoleContextCommitsResult>(
    getConsoleContextCommits({ query }),
  )
}
