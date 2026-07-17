import { useState } from 'react'

interface ScenarioMemory {
  id: string
  type: string
  scope: string
  subject: string
  content: string
  status: string
  source_authority: number
  source_timestamp: string
  supersedes_id: string | null
}

interface ScenarioSummary {
  memory_firewall_passed: boolean
  agent_behaviour_passed: boolean
  old_status: string
  new_status: string
  poison_status: string
  stateless_action: string
  memory_action: string
  recalled_memory_id: string | null
  recalled_ids: string[]
  rejected_count: number
  packed_count: number
  token_budget_used: number
  demo_passed: boolean
}

interface ScenarioProposal {
  action: string
  service: string
  evidence: string
  risk: string
  approval_required: boolean
  status: string
  recalled_memory_ids?: string[]
  insufficient_evidence?: boolean
}

interface ScenarioRun {
  run_id: string
  proposal: ScenarioProposal | null
  events: any[]
}

interface Scenario {
  tenant: string
  alert: {
    service: string
    symptom: string
    context: string
    severity: string
  }
  memories: {
    old: ScenarioMemory
    new: ScenarioMemory
    poison: ScenarioMemory
  }
  summary: ScenarioSummary
  recalled_memory: ScenarioMemory | null
  demo_passed: boolean
  stateless: ScenarioRun
  memory: ScenarioRun
}

function statusClass(status: string) {
  if (status === 'active' || status === 'simulated_safe') return 'bg-green-100 text-green-800'
  if (status === 'superseded') return 'bg-yellow-100 text-yellow-800'
  if (status === 'quarantined') return 'bg-red-100 text-red-800'
  return 'bg-gray-100'
}

const PHASES: { key: keyof Scenario['memories']; label: string; desc: string }[] = [
  { key: 'old', label: 'Session 1 — Older approved procedure', desc: 'Stored first; later superseded by a newer, safer procedure.' },
  { key: 'new', label: 'Session 2 — Newer safe procedure', desc: 'Approved-and-simulated; supersedes the older procedure.' },
  { key: 'poison', label: 'Session 3 — Poison attempt', desc: 'Untrusted external instruction is quarantined.' },
]

export default function JudgeDemo() {
  const [scenario, setScenario] = useState<Scenario | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const runScenario = async () => {
    setLoading(true)
    setError(null)
    setScenario(null)
    try {
      const res = await fetch('/api/demo/judge', { method: 'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setScenario(await res.json())
    } catch (err: any) {
      setError(err.toString())
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="bg-white p-6 rounded shadow mb-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-bold">Judge Demo: Temporal Memory Firewall</h2>
          <p className="text-sm text-gray-600">
            One coherent flow: prior approved-and-simulated sessions, supersession, poison quarantine, and a fresh incident response.
          </p>
        </div>
        <button
          className="bg-indigo-600 text-white px-4 py-2 rounded disabled:opacity-50"
          onClick={runScenario}
          disabled={loading}
        >
          {loading ? 'Running...' : 'Run Judge Demo'}
        </button>
      </div>

      {error && <p className="text-red-600 text-sm mb-4">{error}</p>}

      {!scenario && !loading && (
        <p className="text-gray-500 text-sm">Click the button to run the judge-facing demo on a fresh isolated tenant.</p>
      )}

      {scenario && (
        <div className="space-y-6">
          <div className={`p-3 rounded font-semibold ${scenario.summary.demo_passed ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
            {scenario.summary.demo_passed ? 'PASS' : 'FAIL'} — Memory firewall: {scenario.summary.memory_firewall_passed ? 'PASS' : 'FAIL'}; Agent behaviour: {scenario.summary.agent_behaviour_passed ? 'PASS' : 'FAIL'}.
          </div>

          <div className="relative border-l-2 border-indigo-200 ml-3 space-y-6 pl-6">
            {PHASES.map((phase, idx) => {
              const m = scenario.memories[phase.key]
              return (
                <div key={phase.key} className="relative">
                  <span className="absolute -left-[31px] top-0 flex h-5 w-5 items-center justify-center rounded-full bg-indigo-600 text-white text-xs">{idx + 1}</span>
                  <h3 className="font-semibold text-sm">{phase.label}</h3>
                  <p className="text-xs text-gray-500">{phase.desc}</p>
                  <p className="text-xs text-gray-600 line-clamp-3 mt-1" title={m.content}>{m.content}</p>
                  <span className={`inline-block mt-1 px-2 py-0.5 rounded text-xs ${statusClass(m.status)}`}>{m.status}</span>
                </div>
              )
            })}
            <div className="relative">
              <span className="absolute -left-[31px] top-0 flex h-5 w-5 items-center justify-center rounded-full bg-blue-600 text-white text-xs">4</span>
              <h3 className="font-semibold text-sm">Session 4 — Fresh incident</h3>
              <p className="text-xs text-gray-600">{scenario.alert.symptom}: {scenario.alert.context}</p>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="border rounded p-4">
              <h3 className="font-semibold text-blue-700 mb-2">Stateless (no memory)</h3>
              <p className="text-sm mb-2"><strong>Action:</strong> {scenario.stateless.proposal?.action || 'none'}</p>
              <p className="text-xs text-gray-600 line-clamp-4" title={scenario.stateless.proposal?.evidence || ''}>
                {scenario.stateless.proposal?.evidence || 'No evidence.'}
              </p>
            </div>
            <div className="border rounded p-4 bg-green-50">
              <h3 className="font-semibold text-green-700 mb-2">Memory mode</h3>
              <p className="text-sm mb-2"><strong>Action:</strong> {scenario.memory.proposal?.action || 'none'}</p>
              <p className="text-xs text-gray-600 line-clamp-4" title={scenario.memory.proposal?.evidence || ''}>
                {scenario.memory.proposal?.evidence || 'No evidence.'}
              </p>
              {scenario.recalled_memory ? (
                <p className="text-xs text-gray-500 mt-2">
                  Recalled memory: <span className="font-mono">{scenario.recalled_memory.id.slice(0, 8)}</span>
                </p>
              ) : (
                <p className="text-xs text-red-600 mt-2">Warning: expected memory was not in the actual recall trace.</p>
              )}
            </div>
          </div>

          <div>
            <h3 className="font-semibold mb-2">Memory pack result</h3>
            <div className="flex items-center gap-4 text-sm">
              <div className="flex-1">
                <div className="flex justify-between text-xs mb-1">
                  <span>Token budget</span>
                  <span>{scenario.summary.token_budget_used} / 800</span>
                </div>
                <div className="w-full bg-gray-200 rounded h-3">
                  <div
                    className="bg-blue-500 h-3 rounded"
                    style={{ width: `${Math.min(100, Math.round((scenario.summary.token_budget_used / 800) * 100))}%` }}
                  ></div>
                </div>
              </div>
              <div className="text-center">
                <div className="text-2xl font-bold text-green-600">{scenario.summary.packed_count}</div>
                <div className="text-xs text-gray-500">packed</div>
              </div>
              <div className="text-center">
                <div className="text-2xl font-bold text-red-600">{scenario.summary.rejected_count}</div>
                <div className="text-xs text-gray-500">rejected</div>
              </div>
            </div>
            <p className="text-xs text-gray-600 mt-2">
              Recalled IDs: {scenario.summary.recalled_ids.length > 0 ? scenario.summary.recalled_ids.join(', ') : 'none'}.
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
