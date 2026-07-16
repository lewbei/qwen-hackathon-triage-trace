import { useState } from 'react'

const SERVICES = ['cart-service', 'payment-service', 'notification-service', 'unknown-service']

interface Proposal {
  action: string
  service: string
  evidence: string
  risk: string
  approval_required: boolean
  status: string
}

function App() {
  const [service, setService] = useState(SERVICES[0])
  const [symptom, setSymptom] = useState('High error rate and slow checkout')
  const [context, setContext] = useState('Started after Redis latency spike')
  const [mode, setMode] = useState<'stateless' | 'memory'>('stateless')
  const [result, setResult] = useState<Proposal | null>(null)
  const [events, setEvents] = useState<string[]>([])
  const [loading, setLoading] = useState(false)

  const run = async () => {
    setLoading(true)
    setResult(null)
    setEvents([])
    try {
      const res = await fetch(`/api/agent/runs?mode=${mode}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tenant: 'default', service, symptom, context }),
      })
      const data = await res.json()
      setResult(data.proposal)
      setEvents(data.events.map((e: any) => `[${e.event_type}] ${JSON.stringify(e.payload)}`))
    } catch (err) {
      setEvents([`Error: ${err}`])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 p-8">
      <h1 className="text-3xl font-bold mb-6">TriageTrace</h1>
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
          <h2 className="font-semibold mb-2">Proposal</h2>
          {result ? (
            <div className="space-y-2">
              <p><strong>Action:</strong> {result.action}</p>
              <p><strong>Risk:</strong> {result.risk}</p>
              <p><strong>Status:</strong> {result.status}</p>
              <p><strong>Evidence:</strong> {result.evidence}</p>
            </div>
          ) : (
            <p className="text-gray-500">No proposal yet.</p>
          )}
          <h2 className="font-semibold mt-6 mb-2">Events</h2>
          <pre className="bg-slate-100 p-2 rounded text-xs overflow-auto max-h-96">{events.join('\n')}</pre>
        </div>
      </div>
    </div>
  )
}

export default App
