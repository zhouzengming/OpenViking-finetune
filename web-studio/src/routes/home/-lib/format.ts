import {
  TOKEN_QUICK_RANGE_DAYS,
  TOKEN_SERIES_DAYS,
} from '../-constants/dashboard'
import type { TokenDateRange, TokenRangeOverride } from '../-types/dashboard'

const DATE_KEY_PATTERN = /^\d{4}-\d{2}-\d{2}$/

export function asRecord(v: unknown): Record<string, unknown> {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : {}
}

export function asArray(v: unknown): unknown[] {
  return Array.isArray(v) ? v : []
}

export function asNumber(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0
}

export function asString(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

export function formatNumber(value: unknown): string {
  return asNumber(value).toLocaleString()
}

export function parseDateKey(value: string | undefined): Date {
  const fallback = new Date()
  if (!value) return fallback
  const [year, month, day] = value.split('-').map(Number)
  if (!year || !month || !day) return fallback
  return new Date(year, month - 1, day)
}

export function getViewerTimezone(): string {
  // Falls back to UTC when Intl is unavailable or returns an empty string
  // (e.g. very old Safari, jest jsdom edge cases).
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

export function formatDateInTimezone(d: Date, timeZone: string): string {
  // `en-CA` produces YYYY-MM-DD, which matches the backend `date` query
  // parameter shape exactly.
  return new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(d)
}

export function getTodayDateKey(
  now: Date = new Date(),
  timeZone?: string,
): string {
  return formatDateInTimezone(now, timeZone ?? getViewerTimezone())
}

export function getLastDaysRange(
  days: number,
  timeZone?: string,
  now: Date = new Date(),
): TokenDateRange {
  const tz = timeZone ?? getViewerTimezone()
  // Walk back `days - 1` UTC midnights from now and convert each instant in
  // the viewer's tz; using millisecond arithmetic keeps the math safe across
  // DST transitions because we never inspect a local hour.
  const dayMs = 24 * 60 * 60 * 1000
  const earlier = new Date(now.getTime() - (days - 1) * dayMs)
  return {
    endDate: formatDateInTimezone(now, tz),
    startDate: formatDateInTimezone(earlier, tz),
  }
}

export function resolveTokenRange(
  override: TokenRangeOverride,
  now: Date = new Date(),
  timeZone?: string,
): TokenDateRange {
  return override ?? getLastDaysRange(TOKEN_SERIES_DAYS, timeZone, now)
}

export function getActiveTokenQuickDays(
  range: TokenDateRange,
  now: Date = new Date(),
  timeZone?: string,
): (typeof TOKEN_QUICK_RANGE_DAYS)[number] | null {
  for (const days of TOKEN_QUICK_RANGE_DAYS) {
    const candidate = getLastDaysRange(days, timeZone, now)
    if (
      candidate.startDate === range.startDate &&
      candidate.endDate === range.endDate
    ) {
      return days
    }
  }
  return null
}

export function normalizeCustomTokenRange(
  startDate: string,
  endDate: string,
): TokenRangeOverride {
  const start = startDate.trim()
  const end = endDate.trim()
  if (!DATE_KEY_PATTERN.test(start) || !DATE_KEY_PATTERN.test(end)) {
    return null
  }
  return start <= end
    ? { startDate: start, endDate: end }
    : { startDate: end, endDate: start }
}

export function formatShortDate(value: string): string {
  if (!value) return '--'
  const date = new Date(`${value}T00:00:00`)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString(undefined, {
    day: '2-digit',
    month: '2-digit',
  })
}

export function isDisabledPayload(value: unknown): boolean {
  return asRecord(value).enabled === false
}
