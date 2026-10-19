import type {
  ConsoleContextCommitItem,
  ConsoleRetrievalHeatmapItem,
} from '@ov-server/api/v1/console'

export type HomeT = (key: string, options?: Record<string, unknown>) => string

export type {
  ConsoleContextCommitItem as ContextCommitItem,
  ConsoleContextCounts as ContextCounts,
  ConsoleDashboardSummaryResult as ConsoleDashboardSummary,
  ConsoleRetrievalCounts as RetrievalCounts,
  ConsoleRetrievalHeatmapItem as RetrievalHeatmapItem,
  ConsoleSeriesResult as ConsoleSeries,
  ConsoleTokenCounts as TokenCounts,
  ConsoleTokenSeriesItem as TokenSeriesItem,
} from '@ov-server/api/v1/console'

export type HeatmapActivityMode = 'combined' | 'write' | 'retrieval'

export type TokenDateRange = {
  endDate: string
  startDate: string
}

export type TokenRangeOverride = TokenDateRange | null

export type TokenTrendPayload = {
  color?: string
  dataKey?: string
  name?: string
  value?: number
}

export type HeatMapDayValue = {
  count: number
  date: string
  details: Required<ConsoleContextCommitItem>
  retrievalDetails?: Required<ConsoleRetrievalHeatmapItem>
  retrievalTotal?: number
  writeTotal?: number
}

export type CommitHeatmapStats = {
  activeDays: number
  peakCount: number
  peakDate: string
  recentDate: string
}

export type CommitTooltip = {
  item: HeatMapDayValue
  x: number
  y: number
}
