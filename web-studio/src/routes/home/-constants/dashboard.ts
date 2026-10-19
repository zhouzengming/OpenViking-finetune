export const TOKEN_SERIES_DAYS = 14
export const TOKEN_QUICK_RANGE_DAYS = [7, 14, 30] as const
export const COMMIT_SERIES_DAYS = 365

export const TOKEN_COLORS = {
  embedding: 'var(--home-token-embedding)',
  input: 'var(--home-token-input)',
  output: 'var(--home-token-output)',
  rerank: 'var(--home-token-rerank)',
}

export const HEATMAP_MODES = ['combined', 'write', 'retrieval'] as const

/** 写入模式四档色阶（蓝色调），由浅到深。 */
export const HEATMAP_WRITE_COLOR_STOPS = [
  'oklch(0.82 0.07 232)',
  'oklch(0.7 0.1 232)',
  'oklch(0.58 0.13 238)',
  'oklch(0.46 0.13 245)',
] as const

/** 检索模式四档色阶（绿色调），由浅到深。 */
export const HEATMAP_RETRIEVAL_COLOR_STOPS = [
  'oklch(0.82 0.07 148)',
  'oklch(0.7 0.1 148)',
  'oklch(0.58 0.13 152)',
  'oklch(0.46 0.13 156)',
] as const

/** 融合模式四档色阶（蓝绿融合的青色调），由浅到深。 */
export const HEATMAP_COMBINED_COLOR_STOPS = [
  'oklch(0.82 0.07 190)',
  'oklch(0.7 0.1 190)',
  'oklch(0.58 0.13 195)',
  'oklch(0.46 0.13 200)',
] as const

export const HEATMAP_COLOR_STOPS = HEATMAP_WRITE_COLOR_STOPS

export const HEATMAP_MODE_COLOR_STOPS = {
  combined: HEATMAP_COMBINED_COLOR_STOPS,
  retrieval: HEATMAP_RETRIEVAL_COLOR_STOPS,
  write: HEATMAP_WRITE_COLOR_STOPS,
} as const

export const HEATMAP_EMPTY_COLOR = 'var(--heatmap-empty)'
