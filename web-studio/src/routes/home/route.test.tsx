// @vitest-environment jsdom
import type { ComponentType } from 'react'
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { Route } from './route'

const state = vi.hoisted(() => ({
  role: 'user',
  summary: vi.fn(),
  tokens: vi.fn(),
  commits: vi.fn(),
  retrievals: vi.fn(),
}))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      baseUrl: 'http://localhost:1933',
      accountId: 'account-a',
      userId: 'alice',
      apiKey: '',
      adminApiKey: '',
    },
    connectionRole: state.role,
    isConnectionRoleLoading: false,
  }),
}))
vi.mock('./-lib/api', () => ({
  fetchConsoleDashboardSummary: state.summary,
  fetchConsoleTokenSeries: state.tokens,
  fetchConsoleContextCommits: state.commits,
  fetchConsoleRetrievals: state.retrievals,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: { language: 'zh-CN', resolvedLanguage: 'zh-CN' },
    t: (key: string, options?: Record<string, unknown>) => {
      if (options?.writes !== undefined && options?.retrievals !== undefined) {
        return `${key}:${options.date}:${options.writes}:${options.retrievals}`
      }
      return options?.count !== undefined ? `${key}:${options.count}` : key
    },
  }),
}))
beforeEach(() => {
  state.role = 'user'
  state.summary.mockReset().mockResolvedValue({
    context_counts: { total: 0 },
    today_tokens: { total: 0 },
    today_retrievals: { total: 0 },
  })
  state.tokens.mockReset().mockResolvedValue({ items: [] })
  state.commits.mockReset().mockResolvedValue({ items: [] })
  state.retrievals.mockReset().mockResolvedValue({ items: [] })
  vi.stubGlobal('matchMedia', () => ({ matches: true }))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function renderHome() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const Home = Route.options.component as ComponentType & {
    preload?: () => Promise<unknown>
  }
  await Home.preload?.()
  const view = render(
    <QueryClientProvider client={client}>
      <Home />
    </QueryClientProvider>,
  )
  return () => {
    view.unmount()
    client.clear()
  }
}

it.each(['unknown', 'disabled'])(
  'shows unavailable values for %s data',
  async (reason) => {
    if (reason === 'unknown') state.role = 'unknown'
    else state.summary.mockResolvedValue({ enabled: false })
    const close = await renderHome()
    try {
      await waitFor(() => expect(screen.getAllByText('—')).toHaveLength(3), {
        timeout: 2000,
      })
      if (reason === 'unknown') expect(state.summary).not.toHaveBeenCalled()
    } finally {
      close()
    }
  },
)

it('renders successful zero measurements after the response completes', async () => {
  const zero = {
    context_counts: { total: 0 },
    today_tokens: { total: 0 },
    today_retrievals: { total: 0 },
  }
  let complete!: (value: typeof zero) => void
  state.summary.mockImplementation(
    () =>
      new Promise<typeof zero>((resolve) => {
        complete = resolve
      }),
  )
  const close = await renderHome()
  try {
    await waitFor(() => expect(state.summary).toHaveBeenCalled())
    expect(screen.queryAllByText('0')).toHaveLength(0)
    await act(async () => {
      complete(zero)
    })
    await screen.findByText('contextData.files')
    expect(screen.getByText('todayTokens.rerankInput')).toBeTruthy()
    for (const title of [
      'contextData.title',
      'todayTokens.title',
      'todayRetrievals.title',
    ]) {
      const panel = screen
        .getByRole('heading', { name: title })
        .closest('section')!
      expect(
        within(panel).getByText('0', { selector: '.text-4xl span' }),
      ).toBeTruthy()
      expect(within(panel).queryByText('—')).toBeNull()
    }
  } finally {
    close()
  }
})

it('uses each series result when the summary is disabled', async () => {
  state.summary.mockResolvedValue({ enabled: false })
  const close = await renderHome()
  try {
    await waitFor(
      () => expect(screen.getByText('tokenTrend.empty')).toBeTruthy(),
      { timeout: 2000 },
    )
    expect(screen.getByText('contextCommits.combinedEmpty')).toBeTruthy()
  } finally {
    close()
  }
})

it('defaults to leftmost combined heatmap tab and switches between combined, write, and retrieval modes', async () => {
  state.commits.mockResolvedValue({
    start_date: '2026-10-01',
    end_date: '2026-10-14',
    items: [
      {
        date: '2026-10-10',
        hour: 8,
        add_resource: 2,
        add_skill: 1,
        session_add_message: 0,
        session_commit: 0,
        total: 3,
      },
    ],
  })
  state.retrievals.mockResolvedValue({
    start_date: '2026-10-01',
    end_date: '2026-10-14',
    items: [
      {
        date: '2026-10-10',
        hour: 12,
        find: 4,
        search: 1,
        total: 5,
      },
    ],
  })
  const close = await renderHome()
  try {
    const tablist = await screen.findByRole('tablist', {
      name: 'contextCommits.tabs.ariaLabel',
    })
    const tabs = within(tablist).getAllByRole('tab')
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      'contextCommits.tabs.combined',
      'contextCommits.tabs.write',
      'contextCommits.tabs.retrieval',
    ])
    expect(tabs[0]?.getAttribute('aria-selected')).toBe('true')
    await waitFor(() =>
      expect(
        screen.getByRole('heading', {
          name: 'contextCommits.combinedYearlyTotal:8',
        }),
      ).toBeTruthy(),
    )

    fireEvent.click(tabs[1]!)
    expect(tabs[1]?.getAttribute('aria-selected')).toBe('true')
    expect(
      screen.getByRole('heading', {
        name: 'contextCommits.yearlyTotal:3',
      }),
    ).toBeTruthy()

    fireEvent.click(tabs[2]!)
    expect(tabs[2]?.getAttribute('aria-selected')).toBe('true')
    expect(
      screen.getByRole('heading', {
        name: 'contextCommits.retrievalYearlyTotal:5',
      }),
    ).toBeTruthy()
  } finally {
    close()
  }
})

it('refetches token series when quick or custom date range is selected and cleared', async () => {
  const close = await renderHome()
  try {
    await waitFor(() => expect(state.tokens).toHaveBeenCalledWith(null))

    fireEvent.click(
      screen.getByRole('button', { name: 'tokenTrend.quickDays:7' }),
    )
    await waitFor(() =>
      expect(state.tokens).toHaveBeenLastCalledWith(
        expect.objectContaining({
          endDate: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
          startDate: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
        }),
      ),
    )

    fireEvent.change(screen.getByLabelText('tokenTrend.startDate'), {
      target: { value: '2026-09-01' },
    })
    fireEvent.change(screen.getByLabelText('tokenTrend.endDate'), {
      target: { value: '2026-09-10' },
    })
    await waitFor(() =>
      expect(state.tokens).toHaveBeenLastCalledWith({
        endDate: '2026-09-10',
        startDate: '2026-09-01',
      }),
    )

    fireEvent.click(
      screen.getByRole('button', { name: 'tokenTrend.clearRange' }),
    )
    await waitFor(() => expect(state.tokens).toHaveBeenLastCalledWith(null))
  } finally {
    close()
  }
})

it.each(['loading', 'error'])(
  'withholds metric detail values during %s',
  async (stateName) => {
    if (stateName === 'loading')
      state.summary.mockImplementation(() => new Promise(() => {}))
    else state.summary.mockRejectedValue(new Error('Request failed'))
    const close = await renderHome()
    try {
      await waitFor(() => expect(state.summary).toHaveBeenCalled())
      if (stateName === 'error') await screen.findAllByText('requestFailed')
      expect(screen.queryByText('contextData.files')).toBeNull()
      expect(screen.queryAllByText('0')).toHaveLength(0)
    } finally {
      close()
    }
  },
)
