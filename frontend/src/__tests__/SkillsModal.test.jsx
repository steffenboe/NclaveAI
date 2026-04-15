import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import SkillsModal from '../components/SkillsModal'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const REMOTE_SKILL_WITH_POLICY = {
  id: 'remote-1',
  name: 'kubectl',
  description: 'Kubernetes CLI tool',
  enabled: true,
  policy: 'allow { input.argv[0] == "kubectl" }',
  source: 'remote',
}

const REMOTE_SKILL_NO_POLICY = {
  id: 'remote-2',
  name: 'helm',
  description: 'Helm package manager',
  enabled: true,
  policy: null,
  source: 'remote',
}

const LOCAL_SKILL = {
  id: 'local-1',
  name: 'gh',
  description: 'GitHub CLI',
  enabled: true,
  policy: null,
  source: 'local',
}

function mockFetch(skills = [], settings = {}) {
  const defaultSettings = {
    approval_required: false,
    llm_base_url: 'https://example.com',
    has_llm_api_key: false,
    skills_repo_configured: false,
    skills_repo_url: null,
    skills_repo_branch: 'main',
    ...settings,
  }

  global.fetch = vi.fn((url) => {
    if (url === '/api/skills') {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(skills) })
    }
    if (url === '/api/settings') {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(defaultSettings) })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
  })
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('SkillsModal — remote skill detail view', () => {
  beforeEach(() => {
    mockFetch([REMOTE_SKILL_WITH_POLICY, LOCAL_SKILL])
  })

  it('shows a Details button for remote skills', async () => {
    render(<SkillsModal onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('kubectl')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Details' })).toBeInTheDocument()
  })

  it('does not show a Details button for local skills', async () => {
    render(<SkillsModal onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('gh')).toBeInTheDocument())
    // Local skill has Edit, not Details
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument()
    expect(screen.queryAllByRole('button', { name: 'Details' })).toHaveLength(1) // only for remote
  })

  it('detail view is hidden initially', async () => {
    render(<SkillsModal onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('kubectl')).toBeInTheDocument())
    expect(screen.queryByTestId('remote-skill-detail')).not.toBeInTheDocument()
  })

  it('opens detail view when Details is clicked', async () => {
    render(<SkillsModal onClose={() => {}} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Details' })).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'Details' }))
    expect(screen.getByTestId('remote-skill-detail')).toBeInTheDocument()
  })

  it('detail view shows the skill name', async () => {
    render(<SkillsModal onClose={() => {}} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Details' })).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'Details' }))
    const detail = screen.getByTestId('remote-skill-detail')
    expect(detail).toHaveTextContent('kubectl')
  })

  it('detail view shows the skill description', async () => {
    render(<SkillsModal onClose={() => {}} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Details' })).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'Details' }))
    const detail = screen.getByTestId('remote-skill-detail')
    expect(detail).toHaveTextContent('Kubernetes CLI tool')
  })

  it('detail view shows the policy when present', async () => {
    render(<SkillsModal onClose={() => {}} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Details' })).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'Details' }))
    const detail = screen.getByTestId('remote-skill-detail')
    expect(detail).toHaveTextContent('allow { input.argv[0] == "kubectl" }')
  })

  it('detail view shows no-policy note when policy is absent', async () => {
    mockFetch([REMOTE_SKILL_NO_POLICY])
    render(<SkillsModal onClose={() => {}} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Details' })).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'Details' }))
    const detail = screen.getByTestId('remote-skill-detail')
    expect(detail).toHaveTextContent('No policy set')
  })

  it('detail view contains no editable input or textarea elements', async () => {
    render(<SkillsModal onClose={() => {}} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Details' })).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'Details' }))
    const detail = screen.getByTestId('remote-skill-detail')
    expect(detail.querySelectorAll('input')).toHaveLength(0)
    expect(detail.querySelectorAll('textarea')).toHaveLength(0)
  })

  it('closes detail view when Close button is clicked', async () => {
    render(<SkillsModal onClose={() => {}} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Details' })).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'Details' }))
    expect(screen.getByTestId('remote-skill-detail')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByTestId('remote-skill-detail')).not.toBeInTheDocument()
  })

  it('closes detail view when Escape is pressed', async () => {
    render(<SkillsModal onClose={() => {}} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Details' })).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'Details' }))
    expect(screen.getByTestId('remote-skill-detail')).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByTestId('remote-skill-detail')).not.toBeInTheDocument()
  })

  it('closes detail view when Details is clicked again (toggle)', async () => {
    render(<SkillsModal onClose={() => {}} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Details' })).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'Details' }))
    expect(screen.getByTestId('remote-skill-detail')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Details' }))
    expect(screen.queryByTestId('remote-skill-detail')).not.toBeInTheDocument()
  })
})

