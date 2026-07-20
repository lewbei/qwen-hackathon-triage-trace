import { useEffect, useMemo, useState } from 'react'
import type { Alert, DecisionResult, Deployment, Incident, Memory, RunOut, Signal } from './types'
import SequenceWalkthrough from './SequenceWalkthrough'

const TENANT = 'default'
const SERVICE = 'cart-service'

const INCIDENT: Incident = {
  id: 'cart-redis-latency',
  service: SERVICE,
  title: 'Checkout latency spike after Redis rollout',
  severity: 'sev1',
  alert: 'High checkout failure rate and slow response times',
  customerImpact:
    'Customers are seeing checkout timeouts; conversion is dropping in the NA region.',
  owner: 'payments-platform',
  constraints: [
    'Do not restart the payment database.',
    'Human approval is required before scaling production infra.',
    'Preserve audit logs for finance reconciliation.',
  ],
  recentChanges: [
    'Redis cache tier rolled from local LRU to regional cluster',
    'cart-2.3.1 feature flag raised to 60% and rolled back',
  ],
  signals: [
    { source: 'logs', name: 'redis timeout', value: 'ETIMEDOUT redis-cart:6379', status: 'critical' },
  ],
}

const ALERT: Alert = {
  tenant: TENANT,
  service: SERVICE,
  symptom: 'Redis timeout causing high checkout failure rate',
  severity: 'critical',
  context: 'Logs show ETIMEDOUT redis-cart:6379. Last rollout: cart-2.3.1 (rolled_back). Checkout failures exceeded 40 per minute.',
}

function statusClass(status: string) {
  if (status === 'active' || status === 'simulated_safe') return 'bg-emerald-100 text-emerald-800 border-emerald-200'
  if (status === 'superseded') return 'bg-amber-100 text-amber-800 border-amber-200'
  if (status === 'quarantined' || status === 'rejected') return 'bg-rose-100 text-rose-800 border-rose-200'
  if (status === 'pending') return 'bg-indigo-100 text-indigo-800 border-indigo-200'
  return 'bg-slate-100 text-slate-800 border-slate-200'
}

function severityClass(severity: string) {
  if (severity === 'sev1' || severity === 'critical') return 'bg-rose-100 text-rose-800'
  if (severity === 'sev2' || severity === 'warning') return 'bg-amber-100 text-amber-800'
  return 'bg-blue-100 text-blue-800'
}

function signalClass(status: Signal['status']) {
  if (status === 'critical') return 'bg-rose-50 border-rose-200 text-rose-800'
  if (status === 'warning') return 'bg-amber-50 border-amber-200 text-amber-800'
  return 'bg-emerald-50 border-emerald-200 text-emerald-800'
}

function shortDate(iso: string) {
  const d = new Date(iso)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function truncate(text: string, max: number) {
  return text.length > max ? text.slice(0, max - 1) + '…' : text
}

function formatValue(value: number | undefined, unit: string) {
  return value === undefined ? '-' : `${Math.round(value)}${unit}`
}

function memoryLabel(m: Memory) {
  if (m.status === 'quarantined') return 'Quarantined poison attempt'
  if (m.status === 'superseded') return 'Older approved procedure'
  if (m.status === 'simulated_safe' || m.status === 'active') return 'Current approved procedure'
  return m.type
}

function extractToolCalls(run: RunOut | null) {
  const ev = run?.events.find((e) => e.event_type === 'tools.called')
  if (!ev?.payload?.results) return []
  return (ev.payload.results as { tool: string; result: Record<string, unknown> }[]).map((r) => ({
    name: r.tool,
    summary: r.result.runbook ? 'runbook loaded' : r.result.last_deployments ? 'deployments listed' : 'metrics inspected',
  }))
}

async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 180000)
  try {
    const res = await fetch(url, { ...options, signal: controller.signal })
    clearTimeout(timeout)
    if (!res.ok) {
      const body = await res.text()
      throw new Error(`HTTP ${res.status}: ${body}`)
    }
    return (await res.json()) as T
  } catch (err) {
    clearTimeout(timeout)
    throw err
  }
}

function IconDot({ label, tone }: { label: string; tone: 'indigo' | 'rose' | 'amber' | 'emerald' | 'slate' }) {
  const tones = {
    indigo: 'bg-indigo-100 text-indigo-700',
    rose: 'bg-rose-100 text-rose-700',
    amber: 'bg-amber-100 text-amber-700',
    emerald: 'bg-emerald-100 text-emerald-700',
    slate: 'bg-slate-200 text-slate-700',
  }
  return (
    <span className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${tones[tone]}`}>
      {label}
    </span>
  )
}

function MetricChip({ label, value, tone }: { label: string; value: string; tone: 'indigo' | 'rose' | 'amber' | 'emerald' | 'slate' }) {
  const toneStyles = {
    indigo: 'bg-indigo-50 border-indigo-200 text-indigo-900',
    rose: 'bg-rose-50 border-rose-200 text-rose-900',
    amber: 'bg-amber-50 border-amber-200 text-amber-900',
    emerald: 'bg-emerald-50 border-emerald-200 text-emerald-900',
    slate: 'bg-slate-50 border-slate-200 text-slate-900',
  }
  return (
    <div className={`rounded-lg border p-3 ${toneStyles[tone]}`}>
      <p className="text-xs font-medium opacity-80 uppercase tracking-wide">{label}</p>
      <p className="text-2xl font-bold mt-1">{value}</p>
    </div>
  )
}

function Stage({
  label,
  title,
  subtitle,
  active,
  done,
}: {
  label: string
  title: string
  subtitle?: string
  active?: boolean
  done?: boolean
}) {
  const border = active ? 'bg-white border-indigo-300 shadow-sm' : 'bg-slate-50 border-slate-200'
  const iconTone = done ? 'emerald' : active ? 'indigo' : 'slate'
  const iconLabel = done ? '✓' : label
  return (
    <div className={`flex items-center gap-3 p-3 rounded-lg border ${border}`}>
      <IconDot label={iconLabel} tone={iconTone} />
      <div>
        <p className="text-sm font-semibold text-slate-800">{title}</p>
        {subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}
      </div>
    </div>
  )
}

const rubricEvidence = [
  {
    criterion: 'Innovation & AI Creativity',
    weight: '30%',
    headline: 'Temporal memory firewall',
    detail: 'Qwen-powered agent that accumulates approved-and-simulated procedures, supersedes stale memories, and quarantines poison.',
    evidence: 'https://github.com/lewbei/qwen-hackathon-triage-trace/blob/main/docs/architecture.mmd',
  },
  {
    criterion: 'Technical Depth & Engineering',
    weight: '30%',
    headline: 'Lifecycle + retrieval + simulation',
    detail: 'Conflict decision table, advisory locks, pgvector retrieval, token packing, and deterministic simulation gate.',
    evidence: 'https://github.com/lewbei/qwen-hackathon-triage-trace/tree/main/backend/tests',
  },
  {
    criterion: 'Problem Value & Impact',
    weight: '25%',
    headline: 'Safer incident-response agents',
    detail: 'Prevents catastrophic actions from stale runbooks, untrusted external instructions, and stateless reasoning.',
    evidence: 'https://github.com/lewbei/qwen-hackathon-triage-trace/blob/main/README.md',
  },
  {
    criterion: 'Presentation & Documentation',
    weight: '15%',
    headline: 'Judge-ready demo + ECS deployment',
    detail: 'Production triage UI, Alibaba Terraform, CI, and live smoke-tested health endpoint.',
    evidence: 'https://github.com/lewbei/qwen-hackathon-triage-trace/blob/main/deploy/alibaba/README.md',
  },
]

export default function TriageDashboard() {
  const [memories, setMemories] = useState<Memory[]>([])
  const [runbook, setRunbook] = useState<string>('')
  const [incident, setIncident] = useState<Incident>(INCIDENT)
  const [memoryRun, setMemoryRun] = useState<RunOut | null>(null)
  const [statelessRun, setStatelessRun] = useState<RunOut | null>(null)
  const [decision, setDecision] = useState<DecisionResult | null>(null)
  const [initializing, setInitializing] = useState(false)
  const [running, setRunning] = useState(false)
  const [statelessRunning, setStatelessRunning] = useState(false)
  const [deciding, setDeciding] = useState(false)
  const [loadingSnapshot, setLoadingSnapshot] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [feedback, setFeedback] = useState('')

  const loadMemories = async () => {
    const data = await api<Memory[]>(`/api/memories?tenant=${TENANT}`)
    setMemories(data)
    return data
  }

  const buildIncident = (m: { cpu?: number; latency_p99?: number; checkout_failures?: number } | null, d: { last_deployments: Deployment[] } | null) => {
    const signals: Signal[] = [
      { source: 'logs', name: 'redis timeout', value: 'ETIMEDOUT redis-cart:6379', status: 'critical' },
    ]
    if (m) {
      signals.unshift({
        source: 'metrics',
        name: 'checkout failures/min',
        value: String(m.checkout_failures ?? '-'),
        status: m.checkout_failures && m.checkout_failures > 30 ? 'critical' : 'warning',
      })
      signals.unshift({
        source: 'metrics',
        name: 'p99 latency',
        value: formatValue(m.latency_p99, 'ms'),
        status: m.latency_p99 && m.latency_p99 > 2000 ? 'critical' : 'warning',
      })
      signals.unshift({
        source: 'metrics',
        name: 'cpu',
        value: formatValue(m.cpu ? m.cpu * 100 : undefined, '%'),
        status: m.cpu && m.cpu > 0.8 ? 'warning' : 'ok',
      })
    }
    if (d && d.last_deployments.length) {
      const latest = d.last_deployments[0]
      signals.push({
        source: 'deploy',
        name: 'last rollout',
        value: `${latest.version} (${latest.status})`,
        status: latest.status === 'rolled_back' ? 'warning' : 'ok',
      })
    }
    setIncident((prev) => ({ ...prev, signals }))
  }

  const loadSnapshot = async () => {
    setLoadingSnapshot(true)
    setError(null)
    try {
      const [mems, metricsRes, deployRes, runbookRes] = await Promise.all([
        loadMemories(),
        api<{ result: { cpu: number; latency_p99: number; checkout_failures: number } }>('/api/skills/inspect_metrics/invoke', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ service: SERVICE, time_window: '1h' }),
        }),
        api<{ result: { last_deployments: Deployment[] } }>('/api/skills/list_recent_deployments/invoke', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ service: SERVICE }),
        }),
        api<{ result: { runbook: string } }>('/api/skills/read_current_runbook/invoke', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ service: SERVICE }),
        }),
      ])
      setMemories(mems)
      setRunbook(runbookRes.result.runbook)
      buildIncident(metricsRes.result, deployRes.result)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoadingSnapshot(false)
    }
  }

  useEffect(() => {
    loadSnapshot()
  }, [])

  const initializeDemo = async () => {
    setInitializing(true)
    setError(null)
    try {
      await api('/api/demo/setup', { method: 'POST' })
      await loadSnapshot()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setInitializing(false)
    }
  }

  const runBothTriages = async () => {
    setMemoryRun(null)
    setStatelessRun(null)
    setDecision(null)
    setRunning(true)
    setStatelessRunning(true)
    setError(null)
    try {
      const [stateless, memory] = await Promise.all([
        api<RunOut>(`/api/agent/runs?mode=stateless`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(ALERT),
        }),
        api<RunOut>(`/api/agent/runs?mode=memory`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(ALERT),
        }),
      ])
      setStatelessRun(stateless)
      setMemoryRun(memory)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setRunning(false)
      setStatelessRunning(false)
    }
  }

  const submitDecision = async (approved: boolean) => {
    if (!memoryRun) return
    setDeciding(true)
    setError(null)
    try {
      const result = await api<DecisionResult>(`/api/proposals/${memoryRun.id}/decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved, feedback }),
      })
      setDecision(result)
      await loadMemories()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setDeciding(false)
    }
  }

  const recalledMemory = useMemo(() => {
    if (!memoryRun?.proposal?.recalled_memory_ids?.length) return null
    return memories.find((m) => memoryRun.proposal?.recalled_memory_ids?.includes(m.id))
  }, [memoryRun, memories])

  const filteredMemories = useMemo(
    () => memories.filter((m) => m.scope === SERVICE && m.subject === 'checkout_failures'),
    [memories]
  )

  const timeline = useMemo(
    () =>
      filteredMemories
        .filter((m) => ['simulated_safe', 'active', 'superseded', 'quarantined'].includes(m.status))
        .sort((a, b) => new Date(a.source_timestamp).getTime() - new Date(b.source_timestamp).getTime()),
    [filteredMemories]
  )

  const memoryToolCalls = useMemo(() => extractToolCalls(memoryRun), [memoryRun])
  const statelessToolCalls = useMemo(() => extractToolCalls(statelessRun), [statelessRun])

  const memoryPack = useMemo(() => {
    const ev = memoryRun?.events.find((e) => e.event_type === 'memory.packed')
    if (!ev?.payload) return null
    return ev.payload as {
      packed_count: number
      rejected_count: number
      omitted_count: number
      used_tokens: number
      budget: number
      packed_ids: string[]
      rejected_ids: string[]
    }
  }, [memoryRun])

  const scoreOverall = useMemo(() => {
    if (decision?.outcome) return decision.outcome.improved ? 'improved' : 'worsened'
    if (memoryRun?.proposal?.risk === 'high') return 'high risk'
    if (memoryRun?.proposal) return 'awaiting decision'
    return 'not run'
  }, [decision, memoryRun])

  return (
    <div className="min-h-screen bg-slate-100">
      <header className="bg-slate-900 text-white px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <IconDot label="TT" tone="indigo" />
          <div>
            <h1 className="text-lg font-bold leading-tight">TriageTrace</h1>
            <p className="text-xs text-slate-400">Incident command center — cart-service checkout failures</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs bg-slate-800 px-2 py-1 rounded text-slate-300">Qwen Cloud demo</span>
          {memories.length === 0 ? (
            <button
              className="bg-amber-600 hover:bg-amber-700 text-white px-4 py-2 rounded text-sm font-medium disabled:opacity-50"
              onClick={initializeDemo}
              disabled={initializing}
            >
              {initializing ? 'Initializing…' : 'Initialize demo'}
            </button>
          ) : (
            <button
              className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded text-sm font-medium disabled:opacity-50"
              onClick={runBothTriages}
              disabled={running || statelessRunning}
            >
              {running || statelessRunning ? 'Running both modes…' : 'Run both triage modes'}
            </button>
          )}
        </div>
      </header>

      <main className="p-6 max-w-7xl mx-auto space-y-6">
        {error && (
          <div className="bg-rose-50 border-l-4 border-rose-500 p-4 rounded text-rose-800 text-sm">{error}</div>
        )}

        {memories.length === 0 && !loadingSnapshot && !initializing && (
          <div className="bg-white rounded-lg shadow p-8 text-center">
            <h2 className="text-xl font-semibold text-slate-800 mb-2">No incident memory loaded</h2>
            <p className="text-slate-600 max-w-2xl mx-auto mb-6">
              Initialize the cart-service demo to seed the memory firewall: an older procedure that was superseded,
              the current approved procedure, and a quarantined poison attempt.
            </p>
            <button
              className="bg-indigo-600 hover:bg-indigo-700 text-white px-6 py-2 rounded font-medium"
              onClick={initializeDemo}
            >
              Initialize production demo
            </button>
          </div>
        )}

        {loadingSnapshot && (
          <div className="bg-white rounded-lg shadow p-12 text-center">
            <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
            <p className="mt-4 text-slate-600">Loading incident snapshot…</p>
          </div>
        )}

        {memories.length > 0 && (
          <>
            <div className="bg-indigo-50 border border-indigo-100 rounded-lg p-4">
              <h2 className="font-semibold text-indigo-900 mb-1">Why this demo matters</h2>
              <p className="text-sm text-indigo-800 leading-relaxed">
                TriageTrace is a temporal memory firewall. It prevents incident-response agents from
                acting on stale runbooks, untrusted external instructions, or stateless hallucinations.
                Click <strong>Run both triage modes</strong> to see the same incident handled without
                memory and with the memory firewall that recalls the current approved procedure and
                rejects a poisoned instruction.
              </p>
            </div>

            <div className="bg-white rounded-lg shadow p-5 border-l-4 border-rose-500">
              <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <span className={`px-2 py-0.5 rounded text-xs font-bold uppercase ${severityClass(incident.severity)}`}>
                      {incident.severity}
                    </span>
                    <span className="text-xs text-slate-500 font-mono">{incident.id}</span>
                  </div>
                  <h2 className="text-xl font-bold text-slate-900 mb-1">{incident.title}</h2>
                  <p className="text-sm text-slate-700 mb-3">{incident.alert}</p>
                  <p className="text-sm text-slate-600">
                    <strong>Impact:</strong> {incident.customerImpact} <span className="text-slate-400">|</span>{' '}
                    <strong>Owner:</strong> {incident.owner}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2 md:justify-end">
                  {incident.constraints.map((c, i) => (
                    <span key={i} className="text-xs bg-rose-50 text-rose-700 border border-rose-200 px-2 py-1 rounded">
                      {c}
                    </span>
                  ))}
                </div>
              </div>

              <div className="mt-5 pt-4 border-t border-slate-100">
                <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">Signals</p>
                <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
                  {incident.signals.map((s, idx) => (
                    <div key={idx} className={`rounded-lg border p-3 ${signalClass(s.status)}`}>
                      <p className="text-[10px] uppercase tracking-wide opacity-80">{s.source}</p>
                      <p className="text-xs font-medium mt-1">{s.name}</p>
                      <p className="text-lg font-bold">{s.value}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <SequenceWalkthrough
              memoryRun={memoryRun}
              statelessRun={statelessRun}
              memories={memories}
              decision={decision}
              feedback={feedback}
              setFeedback={setFeedback}
              deciding={deciding}
              onApprove={() => submitDecision(true)}
              onReject={() => submitDecision(false)}
            />

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
              <div className="lg:col-span-3 space-y-6">
                <div className="bg-white rounded-lg shadow p-5">
                  <h2 className="font-semibold text-slate-800 mb-3 flex items-center gap-2">
                    <IconDot label="RB" tone="indigo" /> Runbook
                  </h2>
                  <div className="bg-slate-50 border border-slate-200 rounded p-3 text-sm text-slate-700 whitespace-pre-wrap">
                    {runbook}
                  </div>
                  <div className="mt-4">
                    <p className="text-xs font-semibold text-slate-500 uppercase mb-2">Recent changes</p>
                    <ul className="text-sm text-slate-700 list-disc pl-4 space-y-1">
                      {incident.recentChanges.map((c, i) => (
                        <li key={i}>{c}</li>
                      ))}
                    </ul>
                  </div>
                </div>

                <div className="bg-white rounded-lg shadow p-5">
                  <h2 className="font-semibold text-slate-800 mb-4 flex items-center gap-2">
                    <IconDot label="CL" tone="indigo" /> Memory timeline
                  </h2>
                  {timeline.length === 0 ? (
                    <p className="text-sm text-slate-500">No memory history.</p>
                  ) : (
                    <div className="relative border-l-2 border-slate-200 ml-2 space-y-6 pl-5">
                      {timeline.map((m, idx) => (
                        <div key={m.id} className="relative">
                          <span className="absolute -left-[27px] top-0 flex h-4 w-4 items-center justify-center rounded-full bg-indigo-600 text-white text-[10px] font-bold">
                            {idx + 1}
                          </span>
                          <p className="text-xs text-slate-500">{shortDate(m.source_timestamp)}</p>
                          <p className="text-xs font-semibold text-indigo-700">{memoryLabel(m)}</p>
                          <p className="text-sm text-slate-800 mt-1" title={m.content}>
                            {truncate(m.content, 80)}
                          </p>
                          <span className={`inline-block mt-2 px-2 py-0.5 rounded text-xs font-semibold border ${statusClass(m.status)}`}>
                            {m.status}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              <div className="lg:col-span-6 space-y-6">
                <div className="bg-white rounded-lg shadow p-5">
                  <div className="flex items-center justify-between mb-4">
                    <h2 className="font-semibold text-slate-800 flex items-center gap-2">
                      <IconDot label="AI" tone="indigo" /> Triage recommendation
                    </h2>
                    {(memoryRun || statelessRun) && !running && !statelessRunning && (
                      <button
                        className="text-xs bg-slate-100 hover:bg-slate-200 text-slate-800 px-3 py-1.5 rounded border border-slate-300"
                        onClick={runBothTriages}
                      >
                        Re-run both modes
                      </button>
                    )}
                    {(running || statelessRunning) && <span className="text-xs text-slate-500">Running both triage modes…</span>}
                  </div>

                  {(running || statelessRunning) && !memoryRun && !statelessRun && (
                    <div className="text-center py-10">
                      <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
                      <p className="mt-3 text-slate-600">The agent is running stateless and memory modes in parallel…</p>
                    </div>
                  )}

                  {!running && !statelessRunning && !memoryRun && !statelessRun && (
                    <p className="text-slate-500 text-sm py-6 text-center">
                      Click <strong>Run both triage modes</strong> to compare the stateless baseline against the memory firewall.
                    </p>
                  )}

                  {(memoryRun || statelessRun) && !running && !statelessRunning && (
                    <div className="space-y-5">
                      {(memoryRun?.error || statelessRun?.error || memoryRun?.proposal?.error || statelessRun?.proposal?.error || memoryRun?.proposal?.status === 'invalid' || statelessRun?.proposal?.status === 'invalid') && (
                        <div className="bg-rose-50 border border-rose-200 rounded-lg p-4">
                          <h3 className="text-sm font-semibold text-rose-900 mb-2 flex items-center gap-2">
                            <IconDot label="!" tone="rose" /> Triage failed validation
                          </h3>
                          <p className="text-xs text-rose-800">
                            {memoryRun?.error ||
                              memoryRun?.proposal?.error ||
                              statelessRun?.error ||
                              statelessRun?.proposal?.error ||
                              'One of the triage modes returned an invalid proposal.'}
                          </p>
                          <button
                            className="mt-3 bg-rose-600 hover:bg-rose-700 text-white px-4 py-2 rounded text-sm font-medium disabled:opacity-50"
                            onClick={runBothTriages}
                          >
                            Retry both modes
                          </button>
                        </div>
                      )}

                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="border rounded-lg p-4 bg-slate-50">
                          <h3 className="text-sm font-semibold text-slate-600 mb-2 flex items-center gap-2">
                            <IconDot label="S0" tone="slate" /> Without memory
                          </h3>
                          {statelessRun?.error ? (
                            <p className="text-xs text-rose-700">{statelessRun.error}</p>
                          ) : statelessRun?.proposal?.status === 'invalid' ? (
                            <p className="text-xs text-rose-700">{statelessRun.proposal.error || 'Invalid proposal'}</p>
                          ) : statelessRun?.proposal ? (
                            <>
                              <p className="text-sm font-medium text-slate-800 mb-2">{statelessRun.proposal.action}</p>
                              <p className="text-xs text-slate-600 line-clamp-6">{statelessRun.proposal.evidence}</p>
                              {statelessToolCalls.length > 0 && (
                                <div className="mt-3 space-y-1">
                                  <p className="text-[10px] font-semibold text-slate-500 uppercase">Tools used</p>
                                  {statelessToolCalls.map((t, i) => (
                                    <p key={i} className="text-[10px] text-slate-500">
                                      • {t.name}: {t.summary}
                                    </p>
                                  ))}
                                </div>
                              )}
                            </>
                          ) : statelessRunning ? (
                            <p className="text-sm text-slate-500">Running…</p>
                          ) : (
                            <p className="text-sm text-slate-500">No baseline yet.</p>
                          )}
                          {statelessRun?.proposal && (
                            <div className="flex flex-wrap gap-2 mt-3">
                              <span className="px-2 py-1 rounded bg-slate-200 text-slate-800 text-[10px]">Risk: {statelessRun.proposal.risk}</span>
                              <span className="px-2 py-1 rounded bg-slate-200 text-slate-800 text-[10px]">Status: {statelessRun.proposal.status}</span>
                            </div>
                          )}
                        </div>

                        <div className="border rounded-lg p-4 bg-emerald-50 border-emerald-200 relative">
                          {recalledMemory && (
                            <span className="absolute top-2 right-2 bg-emerald-200 text-emerald-900 text-[10px] px-2 py-0.5 rounded font-semibold uppercase">
                              Memory recalled
                            </span>
                          )}
                          <h3 className="text-sm font-semibold text-emerald-900 mb-2 flex items-center gap-2">
                            <IconDot label="M+" tone="emerald" /> With memory
                          </h3>
                          {memoryRun?.error ? (
                            <p className="text-xs text-rose-700">{memoryRun.error}</p>
                          ) : memoryRun?.proposal?.status === 'invalid' ? (
                            <p className="text-xs text-rose-700">{memoryRun.proposal.error || 'Invalid proposal'}</p>
                          ) : memoryRun?.proposal ? (
                            <>
                              <p className="text-sm font-medium text-slate-900 mb-2">{memoryRun.proposal.action}</p>
                              <p className="text-xs text-slate-700 line-clamp-6">{memoryRun.proposal.evidence}</p>
                              {recalledMemory && (
                                <p className="text-xs text-emerald-800 mt-2 font-medium" title={recalledMemory.content}>
                                  Recalled: {truncate(recalledMemory.content, 70)}
                                </p>
                              )}
                              <a
                                href="https://github.com/lewbei/qwen-hackathon-triage-trace/blob/main/docs/ARCHITECTURE.md"
                                target="_blank"
                                rel="noreferrer"
                                className="text-[10px] text-indigo-600 hover:text-indigo-800 mt-2 inline-block font-medium"
                              >
                                Learn more about the memory firewall →
                              </a>
                              {memoryToolCalls.length > 0 && (
                                <div className="mt-3 space-y-1">
                                  <p className="text-[10px] font-semibold text-slate-500 uppercase">Tools used</p>
                                  {memoryToolCalls.map((t, i) => (
                                    <p key={i} className="text-[10px] text-slate-500">
                                      • {t.name}: {t.summary}
                                    </p>
                                  ))}
                                </div>
                              )}
                            </>
                          ) : running ? (
                            <p className="text-sm text-slate-500">Running…</p>
                          ) : (
                            <p className="text-sm text-slate-500">No memory run yet.</p>
                          )}
                          {memoryRun?.proposal && (
                            <div className="flex flex-wrap gap-2 mt-3">
                              <span className="px-2 py-1 rounded bg-slate-200 text-slate-800 text-[10px]">Risk: {memoryRun.proposal.risk}</span>
                              <span className="px-2 py-1 rounded bg-slate-200 text-slate-800 text-[10px]">Status: {memoryRun.proposal.status}</span>
                            </div>
                          )}
                        </div>
                      </div>

                      {memoryPack && (
                        <div className="bg-slate-50 border border-slate-200 rounded-lg p-4">
                          <div className="flex items-center justify-between text-sm mb-2">
                            <span className="font-medium text-slate-700">Memory pack</span>
                            <span className="text-xs text-slate-500">
                              {memoryPack.used_tokens} / {memoryPack.budget} tokens
                            </span>
                          </div>
                          <div className="w-full bg-slate-200 rounded h-2">
                            <div
                              className="bg-indigo-500 h-2 rounded"
                              style={{ width: `${Math.min(100, (memoryPack.used_tokens / memoryPack.budget) * 100)}%` }}
                            ></div>
                          </div>
                          <div className="flex gap-4 mt-3 text-xs text-slate-600">
                            <span className="font-medium text-emerald-700">{memoryPack.packed_count} packed</span>
                            <span className="font-medium text-rose-700">{memoryPack.rejected_count} rejected</span>
                            <span className="font-medium text-amber-700">{memoryPack.omitted_count} omitted</span>
                          </div>
                        </div>
                      )}

                      {memoryRun?.proposal?.status === 'pending' && (
                        <div className="border-t pt-4 mt-4">
                          <label className="block text-sm text-slate-600 mb-2">Operator feedback</label>
                          <input
                            className="w-full border p-2 rounded"
                            value={feedback}
                            onChange={(e) => setFeedback(e.target.value)}
                            placeholder="Why are you approving or rejecting?"
                          />
                          <div className="flex gap-3 mt-3">
                            <button
                              className="bg-emerald-600 hover:bg-emerald-700 text-white px-4 py-2 rounded text-sm font-medium disabled:opacity-50"
                              onClick={() => submitDecision(true)}
                              disabled={deciding}
                            >
                              {deciding ? 'Deciding…' : 'Approve'}
                            </button>
                            <button
                              className="bg-rose-600 hover:bg-rose-700 text-white px-4 py-2 rounded text-sm font-medium disabled:opacity-50"
                              onClick={() => submitDecision(false)}
                              disabled={deciding}
                            >
                              Reject
                            </button>
                          </div>
                        </div>
                      )}

                      {decision?.outcome && (
                        <div
                          className={`mt-4 border rounded-lg p-4 ${
                            decision.outcome.improved ? 'bg-emerald-50 border-emerald-200' : 'bg-rose-50 border-rose-200'
                          }`}
                        >
                          <h3 className="font-semibold text-sm mb-2 flex items-center gap-2">
                            <IconDot label={decision.outcome.improved ? '+' : '-'} tone={decision.outcome.improved ? 'emerald' : 'rose'} />
                            Simulation outcome
                          </h3>
                          <p className={`text-sm font-semibold ${decision.outcome.improved ? 'text-emerald-700' : 'text-rose-700'}`}>
                            {decision.outcome.improved ? 'Predicted to improve' : 'Predicted to worsen'} — score{' '}
                            {decision.outcome.before_score.toFixed(3)} → {decision.outcome.after_score.toFixed(3)} (Δ{' '}
                            {decision.outcome.delta >= 0 ? '+' : ''}
                            {decision.outcome.delta.toFixed(3)})
                          </p>
                          <p className="text-xs text-slate-600 mt-1">{decision.outcome.reasoning}</p>
                          {decision.memory_status && (
                            <p className="text-xs text-slate-600 mt-2">
                              Stored memory: <span className="font-medium">{decision.memory_status}</span>
                            </p>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>

                <div className="bg-white rounded-lg shadow p-5">
                  <h2 className="font-semibold text-slate-800 mb-4 flex items-center gap-2">
                    <IconDot label="TR" tone="indigo" /> Triage trace
                  </h2>
                  {memoryRun || statelessRun ? (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      {statelessRun && (
                        <div className="space-y-3">
                          <p className="text-xs font-semibold text-slate-500 uppercase">Without memory</p>
                          {statelessRun.proposal && (
                            <div className="flex gap-3 items-start">
                              <IconDot label="AI" tone="indigo" />
                              <div>
                                <p className="text-sm font-medium text-slate-800">Qwen proposed: {statelessRun.proposal.action}</p>
                                <p className="text-xs text-slate-500">Risk {statelessRun.proposal.risk}</p>
                              </div>
                            </div>
                          )}
                          {statelessToolCalls.map((t, i) => (
                            <div key={i} className="flex gap-3 items-start">
                              <IconDot label="TC" tone="indigo" />
                              <div>
                                <p className="text-sm font-medium text-slate-800">{t.name}</p>
                                <p className="text-xs text-slate-500">{t.summary}</p>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                      {memoryRun && (
                        <div className="space-y-3">
                          <p className="text-xs font-semibold text-slate-500 uppercase">With memory</p>
                          {memoryPack && (
                            <div className="flex gap-3 items-start">
                              <IconDot label="DB" tone="emerald" />
                              <div>
                                <p className="text-sm font-medium text-slate-800">Memory firewall recalled {memoryPack.packed_count} record(s)</p>
                                <p className="text-xs text-slate-500">
                                  {memoryPack.rejected_count} rejected, {memoryPack.omitted_count} omitted
                                </p>
                              </div>
                            </div>
                          )}
                          {memoryToolCalls.map((t, i) => (
                            <div key={i} className="flex gap-3 items-start">
                              <IconDot label="TC" tone="indigo" />
                              <div>
                                <p className="text-sm font-medium text-slate-800">{t.name}</p>
                                <p className="text-xs text-slate-500">{t.summary}</p>
                              </div>
                            </div>
                          ))}
                          {memoryRun.proposal && (
                            <div className="flex gap-3 items-start">
                              <IconDot label="AI" tone="indigo" />
                              <div>
                                <p className="text-sm font-medium text-slate-800">Qwen proposed: {memoryRun.proposal.action}</p>
                                <p className="text-xs text-slate-500">Risk {memoryRun.proposal.risk}</p>
                              </div>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  ) : (
                    <p className="text-sm text-slate-500">Run triage to see the reasoning trace.</p>
                  )}
                </div>
              </div>

              <div className="lg:col-span-3 space-y-6">
                <div className="bg-white rounded-lg shadow p-5">
                  <h2 className="font-semibold text-slate-800 mb-4 flex items-center gap-2">
                    <IconDot label="PL" tone="indigo" /> Triage pipeline
                  </h2>
                  <div className="space-y-3">
                    <Stage label="A" title="Alert" subtitle={INCIDENT.alert} active={!memoryRun} done={Boolean(memoryRun)} />
                    <Stage label="R" title="Recall" subtitle={memoryPack ? `${memoryPack.packed_count} memory packed` : 'memory firewall'} active={Boolean(memoryRun && !memoryRun.proposal)} done={Boolean(memoryPack)} />
                    <Stage label="R" title="Reason" subtitle={memoryRun?.proposal?.action ? 'Qwen proposed action' : 'Qwen reasoning'} active={Boolean(memoryRun?.proposal)} done={Boolean(memoryRun?.proposal)} />
                    <Stage label="D" title="Decide" subtitle={decision ? decision.status : 'operator approval'} active={Boolean(memoryRun?.proposal?.status === 'pending')} done={Boolean(decision)} />
                    <Stage label="S" title="Simulate" subtitle={decision?.outcome ? decision.outcome.reasoning : 'predicted outcome'} active={Boolean(decision)} done={Boolean(decision?.outcome)} />
                  </div>
                </div>

                <div className="bg-white rounded-lg shadow p-5">
                  <h2 className="font-semibold text-slate-800 mb-4 flex items-center gap-2">
                    <IconDot label="HB" tone="indigo" /> Health board
                  </h2>
                  <div className="grid grid-cols-2 gap-3">
                    <MetricChip label="Overall" value={scoreOverall} tone={scoreOverall === 'improved' ? 'emerald' : scoreOverall === 'high risk' ? 'rose' : 'slate'} />
                    <MetricChip label="Memory" value={memoryPack ? `${memoryPack.packed_count}/${memoryPack.packed_count + memoryPack.rejected_count + memoryPack.omitted_count}` : '-'} tone="indigo" />
                    <MetricChip label="Tools" value={String(memoryToolCalls.length)} tone="amber" />
                    <MetricChip label="Risk" value={memoryRun?.proposal?.risk ?? '-'} tone={memoryRun?.proposal?.risk === 'high' ? 'rose' : 'emerald'} />
                  </div>
                </div>
              </div>
            </div>

            <div className="bg-white rounded-lg shadow p-5">
              <h2 className="font-semibold text-slate-800 mb-4 flex items-center gap-2">
                <IconDot label="JE" tone="indigo" /> Judge evidence
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                {rubricEvidence.map((item) => (
                  <div key={item.criterion} className="border rounded-lg p-4 hover:shadow-sm transition-shadow">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-bold text-indigo-600 uppercase">{item.weight}</span>
                      <IconDot label="EV" tone="slate" />
                    </div>
                    <h3 className="text-sm font-semibold text-slate-800 mb-1">{item.headline}</h3>
                    <p className="text-xs text-slate-500 mb-3">{item.detail}</p>
                    <a
                      href={item.evidence}
                      className="text-xs text-indigo-600 hover:text-indigo-800 font-medium"
                      target="_blank"
                      rel="noreferrer"
                    >
                      {item.evidence}
                    </a>
                  </div>
                ))}
              </div>
            </div>

            <div className="bg-white rounded-lg shadow p-5">
              <h2 className="font-semibold text-slate-800 mb-4 flex items-center gap-2">
                <IconDot label="ML" tone="indigo" /> Memory lens
              </h2>
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left">
                  <thead>
                    <tr className="border-b border-slate-200 text-slate-500">
                      <th className="py-2 pr-4">Status</th>
                      <th className="py-2 pr-4">When</th>
                      <th className="py-2 pr-4">Authority</th>
                      <th className="py-2 pr-4">Type</th>
                      <th className="py-2 pr-4">Scope / Subject</th>
                      <th className="py-2">Content</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredMemories.map((m) => (
                      <tr key={m.id} className="border-b border-slate-100 last:border-0">
                        <td className="py-3 pr-4">
                          <span className={`px-2 py-0.5 rounded text-xs font-semibold border ${statusClass(m.status)}`}>
                            {m.status}
                          </span>
                        </td>
                        <td className="py-3 pr-4 text-slate-600">{shortDate(m.source_timestamp)}</td>
                        <td className="py-3 pr-4">{m.source_authority}</td>
                        <td className="py-3 pr-4 font-medium">{m.type}</td>
                        <td className="py-3 pr-4">
                          <span className="font-mono text-xs">{m.scope}</span> / <span className="font-mono text-xs">{m.subject}</span>
                        </td>
                        <td className="py-3 max-w-lg truncate" title={m.content}>{m.content}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  )
}
