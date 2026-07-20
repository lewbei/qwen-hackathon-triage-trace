import type { DemoScenario } from './types'

interface ScenarioSelectorProps {
  scenarios: DemoScenario[]
  selectedId: string | null
  onSelect: (id: string) => void
  disabled?: boolean
}

const labels: Record<string, string> = {
  'cart-redis-latency': 'Cart / Redis latency — Primary demo',
  'notifications-queue-backlog': 'Notifications / Queue backlog — Learned procedure',
  'payments-psp-failure': 'Payments / PSP failure — Policy-protected failover',
}

export default function ScenarioSelector({ scenarios, selectedId, onSelect, disabled }: ScenarioSelectorProps) {
  return (
    <div className="flex items-center gap-3">
      <label htmlFor="scenario-select" className="text-sm text-slate-300 hidden md:inline">
        Scenario
      </label>
      <select
        id="scenario-select"
        className="bg-slate-800 text-white text-sm rounded px-3 py-2 border border-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:opacity-50"
        value={selectedId ?? ''}
        onChange={(e) => onSelect(e.target.value)}
        disabled={disabled}
      >
        {scenarios.map((s) => (
          <option key={s.id} value={s.id}>
            {labels[s.id] ?? s.title}
          </option>
        ))}
      </select>
    </div>
  )
}
