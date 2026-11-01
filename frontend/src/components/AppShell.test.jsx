import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import AppShell from './AppShell.jsx'

describe('mobile navigation keyboard behavior', () => {
  afterEach(cleanup)

  it('moves focus into the drawer, closes on Escape, and restores toggle focus', () => {
    render(
      <MemoryRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<h1>Overview content</h1>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    )

    const toggle = screen.getByRole('button', { name: 'Open navigation menu' })
    fireEvent.click(toggle)

    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(document.activeElement).toBe(screen.getByRole('link', { name: 'Overview', exact: true }))

    fireEvent.keyDown(document, { key: 'Escape' })

    expect(screen.getByRole('button', { name: 'Open navigation menu' }).getAttribute('aria-expanded'))
      .toBe('false')
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Open navigation menu' }))
  })

  it('keeps keyboard focus within the open drawer when tabbing past an edge', () => {
    render(
      <MemoryRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<h1>Overview content</h1>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Open navigation menu' }))
    const brandLink = screen.getByRole('link', { name: 'FinSight overview' })
    brandLink.focus()
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })

    expect(document.activeElement).toBe(screen.getByRole('link', { name: 'Watchlist' }))
  })
})
