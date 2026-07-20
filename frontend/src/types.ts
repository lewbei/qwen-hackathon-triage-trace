export interface Alert {
  tenant: string
  service: string
  symptom: string
  severity: string
  context: string
}

export interface Deployment {
  version: string
  time: string
  status: string
}

export interface Signal {
  source: 'metrics' | 'logs' | 'deploy'
  name: string
  value: string
  status: 'critical' | 'warning' | 'ok'
}

export interface Incident {
  id: string
  service: string
  title: string
  severity: 'sev1' | 'sev2' | 'sev3'
  alert: string
  customerImpact: string
  owner: string
  constraints: string[]
  recentChanges: string[]
  signals: Signal[]
}

export interface Memory {
  id: string
  tenant: string
  provenance: string
  source_timestamp: string
  source_authority: number
  type: string
  scope: string
  subject: string
  predicate: string
  content: string
  token_count: number
  status: string
  supersedes_id: string | null
  meta?: Record<string, unknown>
}

export interface Proposal {
  action: string
  service: string
  evidence: string
  risk: string
  approval_required: boolean
  status: string
  recalled_memory_ids?: string[]
  insufficient_evidence?: boolean
  error?: string
}

export interface RunEvent {
  event_type: string
  timestamp?: string
  payload?: Record<string, unknown>
  latency_ms?: number | null
  token_usage?: { prompt: number; completion: number; total: number } | null
  model?: string | null
}

export interface RunOut {
  id: string
  tenant: string
  mode: 'stateless' | 'memory'
  alert: Alert
  events: RunEvent[]
  proposal: Proposal | null
  status: string
  error?: string
  decision?: Record<string, unknown>
}

export interface DecisionResult {
  status: string
  outcome?: {
    improved: boolean
    before_score: number
    after_score: number
    delta: number
    reasoning: string
    before_metrics?: Record<string, number>
    after_metrics?: Record<string, number>
  }
  memory_id?: string
  memory_status?: string
}

export interface MetricSignalDefinition {
  source: 'metrics' | 'logs' | 'deploy'
  name: string
  metric_key: string
  unit: string
  severity_threshold?: number
  status: 'critical' | 'warning' | 'ok'
}

export interface DemoScenario {
  id: string
  title: string
  service: string
  severity: string
  description: string
  incident: Incident
  alert: Alert
  memorySubject: string
  memoryPredicate: string
  metricSignals: MetricSignalDefinition[]
  expectedOutcome: 'simulated_safe' | 'safe_decline'
}
