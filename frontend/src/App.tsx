import { useEffect, useState } from 'react'
import AccumulationDemo from './AccumulationDemo'
import WinningDemo from './WinningDemo'

const SERVICES = ['cart-service', 'payment-service', 'notification-service', 'unknown-service']

interface Memory {
  id: string
  type: string
  scope: string
  subject: string
  predicate: string
  content: string
  status: string
  token_count: number
  source_authority: number
  utility: number
}

interface Proposal {
  action: string
  service: string
  evidence: string
  risk: string
  approval_required: boolean
  status: string
  recalled_memory_ids?: string[]
}

interface Event {
  event_type: string
  timestamp: string
  payload: any
  model?: string | null
  token_usage?: { prompt: number; completion: number; total: number } | null
  latency_ms?: number | null
}

interface PackMeta {
  used_tokens: number
  budget: number
  packed: string[]
  omitted: string[]
  rejected: string[]
  candidates?: number
  selected?: number
}

interface Outcome {
  before_metrics: Record<string, number>
  after_metrics: Record<string, number>
  before_score: number
  after_score: number
  delta: number
  improved: boolean
  reasoning: string
}

function formatEvent(e: Event): string {
  const parts = [`[${e.event_type}]`]
  if (e.model) parts.push(`model=${e.model}`)
  if (e.token_usage) parts.push(`tokens=${e.token_usage.total}`)
  if (e.latency_ms) parts.push(`latency=${Math.round(e.latency_ms)}ms`)
  parts.push(JSON.stringify(e.payload))
  return parts.join(' ')
}

function budgetPercent(meta: PackMeta | null) {
  if (!meta || !meta.budget) return 0
  return Math.min(100, Math.round((meta.used_tokens / meta.budget) * 100))
}

function App() {
  const [service, setService] = useState(SERVICES[0])
  const [symptom, setSymptom] = useState('High error rate and slow checkout')
  const [context, setContext] = useState('Started after Redis latency spike')
  const [mode, setMode] = useState<'stateless' | 'memory'>('stateless')
  const [runId, setRunId] = useState<string | null>(null)
  const [result, setResult] = useState<Proposal | null>(null)
  const [events, setEvents] = useState<Event[]>([])
  const [loading, setLoading] = useState(false)
  const [memories, setMemories] = useState<Memory[]>([])
  const [feedback, setFeedback] = useState('')
  const [packMeta, setPackMeta] = useState<PackMeta | null>(null)
  const [outcome, setOutcome] = useState<Outcome | null>(null)

  const loadMemories = async () => {
    const res = await fetch('/api/memories?tenant=default')
    if (res.ok) setMemories(await res.json())
  }

  const resetDemo = async () => {
    setLoading(true)
    const res = await fetch('/api/demo/reset', { method: 'POST' })
    if (res.ok) {
      await loadMemories()
      setResult(null)
      setEvents([])
      setRunId(null)
      setPackMeta(null)
      setOutcome(null)
    }
    setLoading(false)
  }

  useEffect(() => {
    loadMemories()
  }, [])

  const run = async () => {
    setLoading(true)
    setResult(null)
    setEvents([])
    setRunId(null)
    setPackMeta(null)
    setOutcome(null)
    try {
      const res = await fetch(`/api/agent/runs?mode=${mode}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tenant: 'default', service, symptom, context }),
      })
      const data = await res.json()
      setRunId(data.id)
      setResult(data.proposal)
      setEvents(data.events || [])
      const packed = (data.events || []).find((e: Event) => e.event_type === 'memory.packed')
      if (packed && packed.payload) {
        setPackMeta({
          used_tokens: packed.payload.used_tokens || 0,
          budget: packed.payload.budget || 800,
          packed: packed.payload.packed_ids || [],
          omitted: packed.payload.omitted_ids || [],
          rejected: packed.payload.rejected_ids || [],
          candidates: packed.payload.candidates,
          selected: packed.payload.selected,
        })
      }
    } catch (err: any) {
      setEvents([{ event_type: 'error', timestamp: new Date().toISOString(), payload: err.toString() }])
    } finally {
      setLoading(false)
    }
  }

  const decide = async (approved: boolean) => {
    if (!runId) return
    const res = await fetch(`/api/proposals/${runId}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approved, feedback }),
    })
    if (res.ok) {
      const data = await res.json()
      setResult((r) => (r ? { ...r, status: data.status } : r))
      setOutcome(data.outcome || null)
      await loadMemories()
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 p-8">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-3xl font-bold">TriageTrace</h1>
          <p className="text-sm text-gray-600">A temporal memory firewall for incident-response agents.</p>
        </div>
        <button className="text-sm bg-slate-200 px-3 py-1 rounded" onClick={resetDemo} disabled={loading}>Reset Demo</button>
      </div>
      <WinningDemo />
      <AccumulationDemo />
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-white p-4 rounded shadow">
          <h2 className="font-semibold mb-2">Incident</h2>
          <label className="block text-sm mb-1">Service</label>
          <select className="w-full border p-2 rounded mb-3" value={service} onChange={(e) => setService(e.target.value)}>
            {SERVICES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <label className="block text-sm mb-1">Symptom</label>
          <input className="w-full border p-2 rounded mb-3" value={symptom} onChange={(e) => setSymptom(e.target.value)} />
          <label className="block text-sm mb-1">Context</label>
          <input className="w-full border p-2 rounded mb-3" value={context} onChange={(e) => setContext(e.target.value)} />
          <div className="flex gap-2 mb-3">
            <button className={`px-4 py-2 rounded ${mode === 'stateless' ? 'bg-blue-600 text-white' : 'bg-gray-200'}`} onClick={() => setMode('stateless')}>Stateless</button>
            <button className={`px-4 py-2 rounded ${mode === 'memory' ? 'bg-green-600 text-white' : 'bg-gray-200'}`} onClick={() => setMode('memory')}>Memory</button>
          </div>
          <button className="w-full bg-slate-800 text-white py-2 rounded" onClick={run} disabled={loading}>{loading ? 'Running...' : 'Run'}</button>
        </div>
        <div className="bg-white p-4 rounded shadow md:col-span-2">
          <h2 className="font-semibold mb-2">Proposal {runId && <span className="text-xs text-gray-500">({runId})</span>}</h2>
          {result ? (
            <div className="space-y-2">
              <p><strong>Action:</strong> {result.action}</p>
              <p><strong>Service:</strong> {result.service}</p>
              <p><strong>Risk:</strong> <span className={`px-2 py-0.5 rounded text-xs ${result.risk === 'high' ? 'bg-red-100 text-red-800' : result.risk === 'medium' ? 'bg-yellow-100 text-yellow-800' : 'bg-green-100 text-green-800'}`}>{result.risk}</span></p>
              <p><strong>Status:</strong> {result.status}</p>
              <p><strong>Evidence:</strong> {result.evidence}</p>
              {result.recalled_memory_ids && result.recalled_memory_ids.length > 0 && (
                <p><strong>Recalled memories:</strong> {result.recalled_memory_ids.join(', ')}</p>
              )}
              {packMeta && (
                <div className="mt-3">
                  <div className="flex justify-between text-xs mb-1">
                    <span>Memory token budget</span>
                    <span>{packMeta.used_tokens} / {packMeta.budget} ({budgetPercent(packMeta)}%)</span>
                  </div>
                  <div className="w-full bg-gray-200 rounded h-3">
                    <div className="bg-blue-500 h-3 rounded" style={{ width: `${budgetPercent(packMeta)}%` }}></div>
                  </div>
                  <div className="text-xs text-gray-600 mt-1">
                    candidates={packMeta.candidates ?? '?'} selected={packMeta.selected ?? '?'} packed={packMeta.packed.length} omitted={packMeta.omitted.length} rejected={packMeta.rejected.length}
                  </div>
                </div>
              )}
              {outcome && (
                <div className="mt-4 border rounded p-3 bg-gray-50">
                  <h3 className="font-semibold text-sm mb-1">Simulated outcome</h3>
                  <p className={`text-sm font-semibold ${outcome.improved ? 'text-green-700' : 'text-red-700'}`}>
                    {outcome.improved ? 'Simulated safe — predicted metrics improve' : 'Rejected by simulation — predicted metrics worsen'}
                  </p>
                  <p className="text-xs text-gray-600 mb-2">{outcome.reasoning}</p>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div>
                      <p className="font-semibold">Before score</p>
                      <p>{outcome.before_score}</p>
                    </div>
                    <div>
                      <p className="font-semibold">After score</p>
                      <p>{outcome.after_score} ({outcome.delta >= 0 ? '+' : ''}{outcome.delta})</p>
                    </div>
                  </div>
                </div>
              )}
              {result.status === 'pending' && (
                <div className="mt-4">
                  <input className="w-full border p-2 rounded mb-2" placeholder="Operator feedback" value={feedback} onChange={(e) => setFeedback(e.target.value)} />
                  <div className="flex gap-2">
                    <button className="bg-green-600 text-white px-4 py-2 rounded" onClick={() => decide(true)}>Approve</button>
                    <button className="bg-red-600 text-white px-4 py-2 rounded" onClick={() => decide(false)}>Reject</button>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <p className="text-gray-500">No proposal yet.</p>
          )}
          <h2 className="font-semibold mt-6 mb-2">Events</h2>
          <pre className="bg-slate-100 p-2 rounded text-xs overflow-auto max-h-48">{events.map(formatEvent).join('\n')}</pre>
        </div>
        <div className="bg-white p-4 rounded shadow md:col-span-3">
          <h2 className="font-semibold mb-2">Memory Lens</h2>
          <div className="overflow-auto max-h-96">
            <table className="w-full text-sm text-left">
              <thead>
                <tr className="border-b"><th>Status</th><th>Type</th><th>Scope</th><th>Subject</th><th>Authority</th><th>Utility</th><th>Content</th><th>Tokens</th></tr>
              </thead>
              <tbody>
                {memories.map((m) => (
                  <tr key={m.id} className="border-b">
                    <td className="px-1 py-1"><span className={`px-2 py-0.5 rounded text-xs ${m.status === 'active' || m.status === 'simulated_safe' ? 'bg-green-100 text-green-800' : m.status === 'quarantined' ? 'bg-red-100 text-red-800' : m.status === 'superseded' ? 'bg-yellow-100 text-yellow-800' : 'bg-gray-100'}`}>{m.status}</span></td>
                    <td className="px-1 py-1">{m.type}</td>
                    <td className="px-1 py-1">{m.scope}</td>
                    <td className="px-1 py-1">{m.subject}</td>
                    <td className="px-1 py-1">{m.source_authority}</td>
                    <td className="px-1 py-1">{m.utility ? m.utility.toFixed(2) : '0.00'}</td>
                    <td className="px-1 py-1 max-w-md truncate" title={m.content}>{m.content}</td>
                    <td className="px-1 py-1">{m.token_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}

export default App
