import { lazy, Suspense } from 'react'
import { CalendarRange, X } from 'lucide-react'

import { Skeleton } from '#/components/ui/skeleton'
import { cn } from '#/lib/utils'

import { TOKEN_QUICK_RANGE_DAYS } from '../-constants/dashboard'
import type {
  ConsoleSeries,
  HomeT,
  TokenDateRange,
  TokenRangeOverride,
  TokenSeriesItem,
} from '../-types/dashboard'
import {
  getActiveTokenQuickDays,
  getLastDaysRange,
  getTodayDateKey,
  isDisabledPayload,
  normalizeCustomTokenRange,
  resolveTokenRange,
} from '../-lib/format'
import { normalizeTokenSeries } from '../-lib/normalize'
import { EmptyState, Panel, SectionHeading } from './panel'

const TokenTrendChart = lazy(() =>
  import('./token-trend-chart').then((module) => ({
    default: module.TokenTrendChart,
  })),
)

export function TokenRangeControl({
  hasOverride,
  onChange,
  range,
  t,
}: {
  hasOverride: boolean
  onChange?: (next: TokenRangeOverride) => void
  range: TokenDateRange
  t: HomeT
}) {
  const now = new Date()
  const todayDate = getTodayDateKey(now)
  const activeQuickDays = getActiveTokenQuickDays(range, now)
  const maxStartDate = range.endDate < todayDate ? range.endDate : todayDate

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <div className="inline-flex items-center gap-1">
        {TOKEN_QUICK_RANGE_DAYS.map((days) => {
          const isActive = activeQuickDays === days
          return (
            <button
              key={days}
              type="button"
              aria-pressed={isActive}
              onClick={() => onChange?.(getLastDaysRange(days))}
              className={cn(
                'inline-flex h-7 items-center rounded-full border px-2.5 text-xs tabular-nums transition-colors',
                isActive
                  ? 'border-border bg-muted/80 font-medium text-foreground'
                  : 'border-transparent text-muted-foreground hover:bg-muted/50 hover:text-foreground',
              )}
            >
              {t('tokenTrend.quickDays', {
                count: days,
                defaultValue: `${days} 天`,
              })}
            </button>
          )
        })}
      </div>

      <div className="inline-flex h-7 items-center gap-1.5 rounded-lg border border-border/80 bg-background px-2.5 text-xs tabular-nums text-foreground shadow-2xs">
        <CalendarRange className="size-3.5 shrink-0 text-muted-foreground" />
        <input
          type="date"
          aria-label={t('tokenTrend.startDate', { defaultValue: '开始日期' })}
          value={range.startDate}
          max={maxStartDate}
          onChange={(event) => {
            const nextStart = event.target.value
            if (!nextStart) {
              onChange?.(null)
              return
            }
            onChange?.(normalizeCustomTokenRange(nextStart, range.endDate))
          }}
          className="bg-transparent text-xs tabular-nums text-foreground outline-none"
        />
        <span className="text-muted-foreground">–</span>
        <input
          type="date"
          aria-label={t('tokenTrend.endDate', { defaultValue: '结束日期' })}
          value={range.endDate}
          min={range.startDate}
          max={todayDate}
          onChange={(event) => {
            const nextEnd = event.target.value
            if (!nextEnd) {
              onChange?.(null)
              return
            }
            onChange?.(normalizeCustomTokenRange(range.startDate, nextEnd))
          }}
          className="bg-transparent text-xs tabular-nums text-foreground outline-none"
        />
        {hasOverride ? (
          <button
            type="button"
            aria-label={t('tokenTrend.clearRange', {
              defaultValue: '恢复默认区间',
            })}
            onClick={() => onChange?.(null)}
            className="ml-0.5 inline-flex size-4 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <X className="size-3" />
          </button>
        ) : null}
      </div>
    </div>
  )
}

export function TokenTrendPanel({
  data,
  disabled: disabledProp,
  disabledMessage,
  isError,
  isLoading,
  onRangeChange,
  rangeOverride = null,
  t,
}: {
  data: ConsoleSeries<TokenSeriesItem> | undefined
  disabled?: boolean
  disabledMessage?: string
  isError: boolean
  isLoading: boolean
  onRangeChange?: (next: TokenRangeOverride) => void
  rangeOverride?: TokenRangeOverride
  t: HomeT
}) {
  const items = normalizeTokenSeries(data?.items)
  const disabled = Boolean(disabledProp) || isDisabledPayload(data)
  const effectiveRange = resolveTokenRange(
    rangeOverride ??
      (data?.start_date && data.end_date
        ? { endDate: data.end_date, startDate: data.start_date }
        : null),
  )

  return (
    <Panel>
      <SectionHeading
        action={
          <TokenRangeControl
            hasOverride={rangeOverride !== null}
            onChange={onRangeChange}
            range={effectiveRange}
            t={t}
          />
        }
        description={t('tokenTrend.description')}
        title={t('tokenTrend.title')}
      />

      {isLoading ? (
        <Skeleton className="h-72 w-full" />
      ) : isError ? (
        <EmptyState>{t('requestFailed')}</EmptyState>
      ) : disabled ? (
        <EmptyState>{disabledMessage ?? t('usageDisabled')}</EmptyState>
      ) : items.length === 0 ? (
        <EmptyState>{t('tokenTrend.empty')}</EmptyState>
      ) : (
        <Suspense fallback={<Skeleton className="h-72 w-full" />}>
          <TokenTrendChart items={items} t={t} />
        </Suspense>
      )}
    </Panel>
  )
}
