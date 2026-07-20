import { useEffect, useMemo, useState } from 'react'
import type { DecisionResult, Memory, RunOut } from './types'

function statusClass(status: string) {
  if (status === 'active' || status === 'simulated_safe') return 'bg-emerald-100 text-emerald-800 border-emerald-200'
  if (status === 'superseded') return 'bg-amber-100 text-amber-800 border-amber-200'
  if (status === 'quarantined' || status === 'rejected') return 'bg-rose-100 text-rose-800 border-rose-200'
  if (status === 'pending') return 'bg-indigo-100 text-indigo-800 border-indigo-200'
  return 'bg-slate-100 text-slate-800 border-slate-200'
}

function truncate(text: string, max: number) {
  return text.length > max ? text.slice(0, max - 1) + '…' : text
}

function shortDate(iso: string) {
  const d = new Date(iso)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

interface SequenceWalkthroughProps {
  memoryRun: RunOut | null
  statelessRun: RunOut | null
  memories: Memory[]
  decision: DecisionResult | null
  feedback: string
  setFeedback: (value: string) => void
  deciding: boolean
  onApprove: () => void
  onReject: () => void
}

export default function SequenceWalkthrough({
  memoryRun,
  statelessRun,
  memories,
  decision,
  feedback,
  setFeedback,
  deciding,
  onApprove,
  onReject,
}: SequenceWalkthroughProps) {
  const [activeStep, setActiveStep] = useState(0)
  const [showFullContext, setShowFullContext] = useState(false)

  const memoryPack = useMemo(() => {
    const ev = memoryRun?.events.find((e) => e.event_type === 'memory.packed')
    if (!ev?.payload) return null
    const payload = ev.payload as {
      packed_count: number
      rejected_count: number
      omitted_count: number
      filtered_count?: number
      used_tokens: number
      budget: number
      packed_ids: string[]
      rejected_ids: string[]
      omitted_ids: string[]
      filtered_ids?: string[]
      filter_reasons?: Record<string, { reason: string; score?: number }>
    }
    return payload
  }, [memoryRun])

  const modelContext = useMemo(() => {
    const ev = memoryRun?.events.find((e) => e.event_type === 'model.context')
    if (!ev?.payload) return null
    return ev.payload as { system?: string; user?: string; memory_lines?: number; total_tokens?: number }
  }, [memoryRun])

  const packedMemories = useMemo(() => {
    if (!memoryPack) return []
    return memoryPack.packed_ids.map((id) => memories.find((m) => m.id === id)).filter(Boolean) as Memory[]
  }, [memoryPack, memories])

  const rejectedMemories = useMemo(() => {
    if (!memoryPack) return []
    return memoryPack.rejected_ids.map((id) => memories.find((m) => m.id === id)).filter(Boolean) as Memory[]
  }, [memoryPack, memories])

  const omittedMemories = useMemo(() => {
    if (!memoryPack) return []
    return memoryPack.omitted_ids.map((id) => memories.find((m) => m.id === id)).filter(Boolean) as Memory[]
  }, [memoryPack, memories])

  const filteredMemories = useMemo(() => {
    if (!memoryPack?.filtered_ids) return []
    return memoryPack.filtered_ids.map((id) => memories.find((m) => m.id === id)).filter(Boolean) as Memory[]
  }, [memoryPack, memories])

  const reasoningEvent = useMemo(() => {
    return memoryRun?.events.find((e) => e.event_type === 'model.reasoning')
  }, [memoryRun])

  const toolCalls = useMemo(() => {
    const ev = memoryRun?.events.find((e) => e.event_type === 'tools.called')
    if (!ev?.payload?.results) return []
    return (ev.payload.results as { tool: string; result: Record<string, unknown> }[]).map((r) => ({
      name: r.tool,
      summary: r.result.runbook ? 'runbook loaded' : r.result.last_deployments ? 'deployments listed' : 'metrics inspected',
    }))
  }, [memoryRun])

  const steps = useMemo(
    () => [
      {
        title: '1. Run an incident',
        description: 'The agent receives the live alert and begins a governed triage run.',
        available: Boolean(memoryRun),
      },
      {
        title: '2. Recall the correct memory',
        description: 'The memory firewall retrieves the current approved procedure for this service.',
        available: Boolean(memoryPack && memoryPack.packed_count > 0),
      },
      {
        title: '3. Block poisoned, stale, and off-topic memories',
        description: 'Lifecycle-rejected, relevance-filtered, and budget-omitted memories are kept out of the context window.',
        available: Boolean(
          memoryPack &&
            (memoryPack.rejected_count > 0 ||
              memoryPack.omitted_count > 0 ||
              (memoryPack.filtered_count ?? 0) > 0)
        ),
      },
      {
        title: '4. Send governed context to Qwen',
        description: 'Only the approved, packed memories and the incident details are sent to the model.',
        available: Boolean(modelContext),
      },
      {
        title: '5. Produce a recommendation',
        description: 'Qwen reasons with the governed context and proposes one safe remediation.',
        available: Boolean(memoryRun?.proposal),
      },
      {
        title: '6. Require human approval',
        description: 'A human operator must approve the proposal before it can be simulated and stored.',
        available: Boolean(memoryRun?.proposal?.status === 'pending'),
      },
      {
        title: '7. Display predictive simulation result',
        description: 'The simulated outcome is shown, and the approved memory is stored for future runs.',
        available: Boolean(decision?.outcome),
      },
    ],
    [memoryRun, memoryPack, modelContext, decision]
  )

  useEffect(() => {
    if (activeStep > 0 && !steps[activeStep].available) {
      const lastAvailable = steps.reduce((acc, s, i) => (s.available ? i : acc), 0)
      setActiveStep(lastAvailable)
    }
  }, [steps, activeStep])

  if (!memoryRun) {
    return (
      <div className="bg-white rounded-lg shadow p-6 border-l-4 border-indigo-500">
        <h2 className="font-semibold text-slate-800 mb-2">Guided incident sequence</h2>
        <p className="text-sm text-slate-600">
          Run the memory-mode triage to see the 7-step interaction sequence the judges are looking for.
        </p>
      </div>
    )
  }

  const StepHeader = ({ title, description }: { title: string; description: string }) => {
    return (
      <div className="mb-4">
        <h2 className="font-semibold text-slate-800">{title}</h2>
        <p className="text-sm text-slate-600">{description}</p>
      </div>
    )
  }

  const renderStep = () => {
    switch (activeStep) {
      case 0:
        return (
          <div>
            <StepHeader title={steps[0].title} description={steps[0].description} />
            <div className="bg-slate-50 border border-slate-200 rounded-lg p-4 space-y-2">
              <p className="text-sm font-medium text-slate-800">Run {memoryRun?.id}</p>
              <p className="text-xs text-slate-600">
                <span className="font-semibold">Service:</span> {memoryRun?.alert.service}
              </p>
              <p className="text-xs text-slate-600">
                <span className="font-semibold">Symptom:</span> {memoryRun?.alert.symptom}
              </p>
              <p className="text-xs text-slate-600">
                <span className="font-semibold">Context:</span> {memoryRun?.alert.context}
              </p>
              {statelessRun && (
                <div className="mt-3 pt-3 border-t border-slate-200">
                  <p className="text-xs font-semibold text-slate-700 mb-1">Baseline (stateless) proposal</p>
                  <p className="text-sm text-slate-700">{statelessRun.proposal?.action || 'No proposal'}</p>
                  <p className="text-xs text-slate-500">Risk: {statelessRun.proposal?.risk || '-'}</p>
                </div>
              )}
            </div>
          </div>
        )
      case 1:
        return (
          <div>
            <StepHeader title={steps[1].title} description={steps[1].description} />
            {memoryPack ? (
              <div className="space-y-3">
                <p className="text-sm text-slate-700">
                  <span className="font-semibold">{memoryPack.packed_count}</span> memory records were packed into the context window ({' '}
                  {memoryPack.used_tokens} / {memoryPack.budget} tokens).
                </p>
                <div className="space-y-2 max-h-64 overflow-y-auto pr-2">
                  {packedMemories.length === 0 && <p className="text-sm text-slate-500">No packed memories.</p>}
                  {packedMemories.map((m) => (
                    <div key={m.id} className="bg-emerald-50 border border-emerald-200 rounded p-3 text-sm">
                      <div className="flex items-center gap-2 mb-1">
                        <span className={`px-2 py-0.5 rounded text-xs font-semibold border ${statusClass(m.status)}`}>{m.status}</span>
                        <span className="text-xs text-slate-500">{shortDate(m.source_timestamp)}</span>
                      </div>
                      <p className="text-slate-800">{m.content}</p>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <p className="text-sm text-slate-500">Memory pack event not available.</p>
            )}
          </div>
        )
      case 2:
        return (
          <div>
            <StepHeader title={steps[2].title} description={steps[2].description} />
            {memoryPack ? (
              <div className="space-y-4 max-h-72 overflow-y-auto pr-2">
                {memoryPack.rejected_count > 0 && (
                  <div className="space-y-2">
                    <p className="text-xs font-semibold text-rose-700 uppercase tracking-wide">Rejected ({memoryPack.rejected_count})</p>
                    {rejectedMemories.length === 0 && <p className="text-sm text-slate-500">Reasons for rejection are in the backend audit log.</p>}
                    {rejectedMemories.map((m) => (
                      <div key={m.id} className="bg-rose-50 border border-rose-200 rounded p-3 text-sm">
                        <div className="flex items-center gap-2 mb-1">
                          <span className={`px-2 py-0.5 rounded text-xs font-semibold border ${statusClass(m.status)}`}>{m.status}</span>
                          <span className="text-xs text-slate-500">{shortDate(m.source_timestamp)}</span>
                        </div>
                        <p className="text-slate-800">{truncate(m.content, 140)}</p>
                        {Boolean(m.meta?.quarantine_reason) && (
                          <p className="text-xs text-rose-700 mt-1">Reason: {String(m.meta?.quarantine_reason)}</p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                {memoryPack.omitted_count > 0 && (
                  <div className="space-y-2">
                    <p className="text-xs font-semibold text-amber-700 uppercase tracking-wide">Omitted due to token budget ({memoryPack.omitted_count})</p>
                    {omittedMemories.map((m) => (
                      <div key={m.id} className="bg-amber-50 border border-amber-200 rounded p-3 text-sm">
                        <div className="flex items-center gap-2 mb-1">
                          <span className={`px-2 py-0.5 rounded text-xs font-semibold border ${statusClass(m.status)}`}>{m.status}</span>
                          <span className="text-xs text-slate-500">{shortDate(m.source_timestamp)}</span>
                        </div>
                        <p className="text-slate-800">{truncate(m.content, 140)}</p>
                        <p className="text-xs text-amber-700 mt-1">Reason: token budget exhausted</p>
                      </div>
                    ))}
                  </div>
                )}
                {(memoryPack.filtered_count ?? 0) > 0 && (
                  <div className="space-y-2">
                    <p className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
                      Relevance filtered ({memoryPack.filtered_count})
                    </p>
                    {filteredMemories.map((m) => (
                      <div key={m.id} className="bg-slate-50 border border-slate-200 rounded p-3 text-sm">
                        <div className="flex items-center gap-2 mb-1">
                          <span className={`px-2 py-0.5 rounded text-xs font-semibold border ${statusClass(m.status)}`}>{m.status}</span>
                          <span className="text-xs text-slate-500">{shortDate(m.source_timestamp)}</span>
                        </div>
                        <p className="text-slate-800">{truncate(m.content, 140)}</p>
                        {(() => {
                          const fr = memoryPack.filter_reasons?.[m.id]
                          if (!fr) return null
                          return (
                            <p className="text-xs text-slate-600 mt-1">
                              Reason: {fr.reason}
                              {fr.score !== undefined && ` (score ${fr.score.toFixed(3)})`}
                            </p>
                          )
                        })()}
                      </div>
                    ))}
                  </div>
                )}
                {memoryPack.rejected_count === 0 && memoryPack.omitted_count === 0 && (memoryPack.filtered_count ?? 0) === 0 && (
                  <p className="text-sm text-slate-500">No memories were blocked, filtered, or omitted.</p>
                )}
              </div>
            ) : (
              <p className="text-sm text-slate-500">Memory pack event not available.</p>
            )}
          </div>
        )
      case 3:
        return (
          <div>
            <StepHeader title={steps[3].title} description={steps[3].description} />
            {modelContext ? (
              <div className="space-y-3">
                <p className="text-sm text-slate-700">
                  <span className="font-semibold">{modelContext.memory_lines || 0}</span> memory lines and the incident description are included in the prompt ({' '}
                  {modelContext.total_tokens || '-'} total tokens).
                </p>
                <div className="bg-slate-900 text-slate-100 rounded-lg p-4 text-xs font-mono whitespace-pre-wrap max-h-80 overflow-y-auto">
                  {showFullContext ? modelContext.system : truncate(modelContext.system || '', 400)}
                </div>
                <button
                  className="text-xs text-indigo-600 hover:text-indigo-800 font-medium"
                  onClick={() => setShowFullContext((v) => !v)}
                >
                  {showFullContext ? 'Show less context' : 'Show full governed context'}
                </button>
              </div>
            ) : (
              <p className="text-sm text-slate-500">Model context event not available.</p>
            )}
          </div>
        )
      case 4:
        return (
          <div>
            <StepHeader title={steps[4].title} description={steps[4].description} />
            <div className="space-y-4">
              {Boolean(reasoningEvent?.payload?.tool_calls) && (
                <div className="bg-slate-50 border border-slate-200 rounded-lg p-4">
                  <p className="text-xs font-semibold text-slate-700 uppercase tracking-wide mb-2">Reasoning steps</p>
                  <ul className="space-y-1 text-sm text-slate-700">
                    {toolCalls.map((t, i) => (
                      <li key={i}>
                        <span className="font-medium">{t.name}:</span> {t.summary}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {memoryRun?.proposal ? (
                <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-4">
                  <p className="text-sm font-semibold text-emerald-900">{memoryRun.proposal.action}</p>
                  <p className="text-sm text-emerald-800 mt-1">{memoryRun.proposal.evidence}</p>
                  <div className="flex gap-2 mt-3">
                    <span className="px-2 py-1 rounded bg-slate-200 text-slate-800 text-xs">Risk: {memoryRun.proposal.risk}</span>
                    <span className="px-2 py-1 rounded bg-slate-200 text-slate-800 text-xs">Status: {memoryRun.proposal.status}</span>
                  </div>
                </div>
              ) : (
                <p className="text-sm text-slate-500">No recommendation produced.</p>
              )}
            </div>
          </div>
        )
      case 5:
        return (
          <div>
            <StepHeader title={steps[5].title} description={steps[5].description} />
            {memoryRun?.proposal?.status === 'pending' ? (
              <div className="space-y-4">
                <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
                  <p className="text-sm text-amber-900 font-medium">Approval required</p>
                  <p className="text-xs text-amber-800 mt-1">
                    The proposal is intentionally gated. Approve to run the predictive simulation and store this as approved experience.
                  </p>
                </div>
                <label className="block text-sm text-slate-600 mb-2">Operator feedback</label>
                <input
                  className="w-full border p-2 rounded text-sm"
                  value={feedback}
                  onChange={(e) => setFeedback(e.target.value)}
                  placeholder="Why are you approving or rejecting?"
                />
                <div className="flex gap-3 mt-3">
                  <button
                    className="bg-emerald-600 hover:bg-emerald-700 text-white px-4 py-2 rounded text-sm font-medium disabled:opacity-50"
                    onClick={onApprove}
                    disabled={deciding}
                  >
                    {deciding ? 'Deciding…' : 'Approve'}
                  </button>
                  <button
                    className="bg-rose-600 hover:bg-rose-700 text-white px-4 py-2 rounded text-sm font-medium disabled:opacity-50"
                    onClick={onReject}
                    disabled={deciding}
                  >
                    Reject
                  </button>
                </div>
              </div>
            ) : decision ? (
              <p className="text-sm text-slate-500">A decision has already been recorded for this run.</p>
            ) : (
              <p className="text-sm text-slate-500">No pending proposal to approve.</p>
            )}
          </div>
        )
      case 6:
        return (
          <div>
            <StepHeader title={steps[6].title} description={steps[6].description} />
            {decision?.outcome ? (
              <div className={`border rounded-lg p-4 ${decision.outcome.improved ? 'bg-emerald-50 border-emerald-200' : 'bg-rose-50 border-rose-200'}`}>
                <p className={`text-sm font-semibold ${decision.outcome.improved ? 'text-emerald-700' : 'text-rose-700'}`}>
                  {decision.outcome.improved ? 'Predicted to improve' : 'Predicted to worsen'} — score{' '}
                  {decision.outcome.before_score.toFixed(3)} → {decision.outcome.after_score.toFixed(3)} (Δ{' '}
                  {decision.outcome.delta >= 0 ? '+' : ''}
                  {decision.outcome.delta.toFixed(3)})
                </p>
                <p className="text-xs text-slate-600 mt-2">{decision.outcome.reasoning}</p>
                {decision.memory_status && (
                  <p className="text-xs text-slate-600 mt-2">
                    Stored memory status: <span className="font-medium">{decision.memory_status}</span>
                  </p>
                )}
              </div>
            ) : (
              <p className="text-sm text-slate-500">Simulation result will appear after operator approval.</p>
            )}
          </div>
        )
      default:
        return null
    }
  }

  return (
    <div className="bg-white rounded-lg shadow p-5 border-t-4 border-indigo-500">
      <h2 className="font-semibold text-slate-800 mb-4">Guided incident sequence</h2>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-1 space-y-2">
          {steps.map((s, i) => {
            const isActive = i === activeStep
            const isAvailable = s.available
            const isDone = isAvailable && i < activeStep
            return (
              <button
                key={i}
                className={`w-full text-left p-3 rounded-lg border text-sm transition-colors ${
                  isActive
                    ? 'bg-indigo-50 border-indigo-300 ring-1 ring-indigo-200'
                    : isDone
                      ? 'bg-emerald-50 border-emerald-200'
                      : isAvailable
                        ? 'bg-white border-slate-200 hover:bg-slate-50'
                        : 'bg-slate-50 border-slate-100 text-slate-400 cursor-not-allowed'
                }`}
                onClick={() => isAvailable && setActiveStep(i)}
                disabled={!isAvailable}
              >
                <p className={`font-semibold ${isActive ? 'text-indigo-900' : isDone ? 'text-emerald-900' : 'text-slate-700'}`}>{s.title}</p>
                <p className="text-xs text-slate-500 mt-0.5">{s.description}</p>
              </button>
            )
          })}
        </div>
        <div className="lg:col-span-2 bg-slate-50 rounded-lg border border-slate-200 p-4">
          {renderStep()}
          <div className="flex justify-between items-center mt-6 pt-4 border-t border-slate-200">
            <button
              className="text-sm text-slate-600 hover:text-slate-800 disabled:text-slate-400 disabled:cursor-not-allowed"
              onClick={() => setActiveStep((s) => Math.max(0, s - 1))}
              disabled={activeStep === 0}
            >
              ← Previous
            </button>
            <div className="flex gap-1">
              {steps.map((s, i) => (
                <span
                  key={i}
                  className={`h-2 w-2 rounded-full ${i === activeStep ? 'bg-indigo-500' : s.available ? 'bg-emerald-400' : 'bg-slate-200'}`}
                />
              ))}
            </div>
            <button
              className="text-sm text-slate-600 hover:text-slate-800 disabled:text-slate-400 disabled:cursor-not-allowed"
              onClick={() => setActiveStep((s) => Math.min(steps.length - 1, s + 1))}
              disabled={activeStep === steps.length - 1 || !steps[activeStep + 1]?.available}
            >
              Next →
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
