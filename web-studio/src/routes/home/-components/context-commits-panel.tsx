import { lazy, Suspense, useMemo, useState } from 'react'

import { Skeleton } from '#/components/ui/skeleton'
import { cn } from '#/lib/utils'

import {
  COMMIT_SERIES_DAYS,
  HEATMAP_MODES,
  HEATMAP_MODE_COLOR_STOPS,
} from '../-constants/dashboard'
import type {
  ConsoleSeries,
  ContextCommitItem,
  HeatmapActivityMode,
  HomeT,
  RetrievalHeatmapItem,
} from '../-types/dashboard'
import {
  formatNumber,
  getLastDaysRange,
  isDisabledPayload,
  parseDateKey,
} from '../-lib/format'
import {
  buildHeatmapPanelColors,
  computeCommitHeatmapStats,
  normalizeCombinedHeatmapData,
  normalizeCommitHeatmapData,
  normalizeRetrievalHeatmapData,
} from '../-lib/normalize'
import { EmptyState, Panel, SectionHeading } from './panel'

const ContextCommitsHeatmap = lazy(() =>
  import('./context-commits-heatmap').then((module) => ({
    default: module.ContextCommitsHeatmap,
  })),
)

export function ContextCommitsPanel({
  data,
  disabled: disabledProp,
  disabledMessage,
  isError,
  isLoading,
  isRetrievalError = false,
  isRetrievalLoading = false,
  retrievalData,
  t,
}: {
  data: ConsoleSeries<ContextCommitItem> | undefined
  disabled?: boolean
  disabledMessage?: string
  isError: boolean
  isLoading: boolean
  isRetrievalError?: boolean
  isRetrievalLoading?: boolean
  retrievalData?: ConsoleSeries<RetrievalHeatmapItem> | undefined
  t: HomeT
}) {
  const [mode, setMode] = useState<HeatmapActivityMode>('combined')

  const writeItems = useMemo(
    () => normalizeCommitHeatmapData(data?.items),
    [data?.items],
  )
  const retrievalItems = useMemo(
    () => normalizeRetrievalHeatmapData(retrievalData?.items),
    [retrievalData?.items],
  )
  const combinedItems = useMemo(
    () => normalizeCombinedHeatmapData(data?.items, retrievalData?.items),
    [data?.items, retrievalData?.items],
  )

  const items =
    mode === 'retrieval'
      ? retrievalItems
      : mode === 'combined'
        ? combinedItems
        : writeItems
  const colorStops = HEATMAP_MODE_COLOR_STOPS[mode]
  const panelColors = useMemo(
    () => buildHeatmapPanelColors(items, colorStops),
    [items, colorStops],
  )
  const totalActivity = useMemo(
    () => items.reduce((total, item) => total + item.count, 0),
    [items],
  )
  const stats = useMemo(() => computeCommitHeatmapStats(items), [items])

  const activeLoading =
    mode === 'retrieval'
      ? isRetrievalLoading
      : mode === 'combined'
        ? isLoading || isRetrievalLoading
        : isLoading
  const activeError =
    mode === 'retrieval'
      ? isRetrievalError
      : mode === 'combined'
        ? isError || isRetrievalError
        : isError
  const disabled =
    Boolean(disabledProp) ||
    (mode === 'retrieval'
      ? isDisabledPayload(retrievalData)
      : mode === 'combined'
        ? isDisabledPayload(data) || isDisabledPayload(retrievalData)
        : isDisabledPayload(data))

  const activeSeries =
    mode === 'retrieval' ? (retrievalData ?? data) : (data ?? retrievalData)
  const range = getLastDaysRange(COMMIT_SERIES_DAYS)
  const rangeLabel =
    activeSeries?.start_date && activeSeries.end_date
      ? `${activeSeries.start_date} - ${activeSeries.end_date}`
      : `${range.startDate} - ${range.endDate}`
  const startDate = parseDateKey(activeSeries?.start_date ?? range.startDate)
  const endDate = parseDateKey(activeSeries?.end_date ?? range.endDate)

  const title =
    !activeLoading && !activeError && !disabled
      ? mode === 'retrieval'
        ? totalActivity > 0
          ? t('contextCommits.retrievalYearlyTotal', {
              count: formatNumber(totalActivity),
            })
          : t('contextCommits.retrievalYearlyEmpty')
        : mode === 'combined'
          ? totalActivity > 0
            ? t('contextCommits.combinedYearlyTotal', {
                count: formatNumber(totalActivity),
              })
            : t('contextCommits.combinedYearlyEmpty')
          : totalActivity > 0
            ? t('contextCommits.yearlyTotal', {
                count: formatNumber(totalActivity),
              })
            : t('contextCommits.yearlyEmpty')
      : mode === 'retrieval'
        ? t('contextCommits.retrievalTitle')
        : mode === 'combined'
          ? t('contextCommits.combinedTitle')
          : t('contextCommits.title')

  const description =
    mode === 'retrieval'
      ? t('contextCommits.retrievalDescription')
      : mode === 'combined'
        ? t('contextCommits.combinedDescription')
        : t('contextCommits.description')

  const emptyLabel =
    mode === 'retrieval'
      ? t('contextCommits.retrievalEmpty')
      : mode === 'combined'
        ? t('contextCommits.combinedEmpty')
        : t('contextCommits.empty')

  return (
    <Panel>
      <SectionHeading
        action={
          <span className="pt-1 text-xs tabular-nums text-muted-foreground">
            {rangeLabel}
          </span>
        }
        description={description}
        title={title}
        titleAddon={
          <div
            aria-label={t('contextCommits.tabs.ariaLabel')}
            className="inline-flex items-center gap-0.5 rounded-full border border-border/70 bg-muted/50 p-0.5 dark:border-[#363638] dark:bg-[#1e1e20]"
            role="tablist"
          >
            {HEATMAP_MODES.map((tabMode) => {
              const active = mode === tabMode
              return (
                <button
                  key={tabMode}
                  aria-selected={active}
                  className={cn(
                    'h-6 cursor-pointer rounded-full px-2.5 text-xs leading-5 transition-all',
                    active
                      ? 'bg-background font-medium text-foreground shadow-xs dark:bg-[#323236]'
                      : 'font-normal text-muted-foreground hover:text-foreground',
                  )}
                  role="tab"
                  type="button"
                  onClick={() => setMode(tabMode)}
                >
                  {t(`contextCommits.tabs.${tabMode}`)}
                </button>
              )
            })}
          </div>
        }
      />

      {activeLoading ? (
        <Skeleton className="h-72 w-full" />
      ) : activeError ? (
        <EmptyState>{t('requestFailed')}</EmptyState>
      ) : disabled ? (
        <EmptyState>{disabledMessage ?? t('usageDisabled')}</EmptyState>
      ) : items.length === 0 ? (
        <EmptyState>{emptyLabel}</EmptyState>
      ) : (
        <Suspense fallback={<Skeleton className="h-72 w-full" />}>
          <ContextCommitsHeatmap
            colorStops={colorStops}
            endDate={endDate}
            items={items}
            mode={mode}
            panelColors={panelColors}
            startDate={startDate}
            stats={stats}
            t={t}
          />
        </Suspense>
      )}
    </Panel>
  )
}
