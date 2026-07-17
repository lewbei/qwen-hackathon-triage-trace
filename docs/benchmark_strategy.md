# TriageTrace Benchmark Strategy

> Research date: 2026-07-17. Sources from Tavily searches over 2025–2026 papers and repos.

## 1. What we tested so far

TriageTrace is now evaluated on three surfaces:

1. **Custom, hand-written adversarial scenario suite** (`backend/evaluations/scenarios.json`, 13 scenarios):

|| Category | Count | What it stresses |
|---|---|---|---|
|| Repeated incident | 4 | Recalling a validated remediation under a token budget |
|| Operator-policy override | 3 | Enforcing hard operator constraints (e.g., never restart DB) |
|| Temporal conflict | 3 | Superseding an old procedure with a newer runbook |
|| Poisoned log | 2 | Ignoring malicious instructions embedded in untrusted logs |
|| Irrelevant overload | 1 | Retrieving the correct memory among five irrelevant observations |

Metrics: correct-action accuracy, policy compliance, latency, tokens, poison/stale recall rates, temporal correctness, and irrelevant-memory intrusion. The latest live run with `qwen3-rerank` is **stateless 23.1% → memory 84.6% accuracy**.

2. **AgentSecurityBench (ASB) memory-poisoning gate** (`backend/evaluations/benchmarks/asb_memorygate.py`):

|| Metric | Value |
|---|---|---|
|| Attack samples | 100 |
|| Normal samples | 20 |
|| True positives | 54 / 100 |
|| False positives | 0 / 20 |
|| Precision | 1.00 |
|| Recall | 0.54 |
|| F1 | 0.70 |
|| Accuracy | 61.7% |

Zero false positives on normal tools is the key result; the false negatives are mostly low-aggression, stealth attack instructions.

3. **MemoryAgentBench conflict-resolution pilot** (`backend/evaluations/benchmarks/memoryagentbench.py`):

|| Setting | Accuracy |
|---|---|---|
|| All facts provided | 4 / 5 (80%) |
|| Top-50 retrieval only | 1 / 5 (20%) |

This confirms reasoning is strong once the relevant facts are surfaced, and it identifies multi-hop retrieval as the next bottleneck.

**Why still keep a custom suite?** No public benchmark currently covers the intersection of real-world incident-response workflow, persistent validated memory, temporal supersession, adversarial memory poisoning, and human-in-the-loop approval. The public benchmarks give externally comparable numbers; the custom suite isolates the specific memory-firewall behaviors TriageTrace ships.

## 2. Public benchmarks that fit

### 2.1 MemoryAgentBench — the closest match for the hackathon track

- **Paper:** *Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions* (Hu, Wang, McAuley, 2025 / ICLR 2026). arXiv 2507.05257.
- **What it tests:** four memory competencies:
  - **Accurate Retrieval (AR)**
  - **Test-Time Learning (TTL)**
  - **Long-Range Understanding (LRU)**
  - **Conflict Resolution (CR)**, including single-hop and multi-hop fact consolidation.
- **Datasets:** `EventQA` and `FactConsolidation` plus reconstructed long-context sets.
- **Why it fits:** It is literally named "MemoryAgentBench" — the same name as the Qwen Cloud track. It stresses the exact behaviors TriageTrace targets: memory writes, updates, retrieval, and conflict handling.
- **Status:** Public GitHub/HuggingFace datasets exist (`OpenDataBox/MemoryData` references configs and `ai-hyz/MemoryAgentBench`).

### 2.2 MemBench — broader memory capability

- **Paper:** *MemBench: Towards More Comprehensive Evaluation on the Memory of LLM-based Agents* (Tan et al., ACL Findings 2025). arXiv 2506.21605.
- **What it tests:** factual and reflective memory across participation and observation scenarios.
- **Metrics:** accuracy, recall, capacity, efficiency.
- **Why it fits:** Useful for proving general memory quality, but less domain-specific than incident response.

### 2.3 AgentSecurityBench (ASB) — adversarial memory + safety

- **Paper:** *Agent Security Bench (ASB): Formalizing and Benchmarking Attacks and Defenses in LLM-based Agents* (Zhang et al., ICLR 2025).
- **What it tests:** 10 application scenarios, 400+ tools, 400 tasks, 23 attack/defense methods across direct injection, observation injection, memory poisoning, and plan-of-thought backdoor. ~1,600 test cases.
- **Metrics:** Attack Success Rate (ASR) and Refusal Rate (RR).
- **Why it fits:** The only public benchmark specifically dedicated to agent memory poisoning. TriageTrace's MemoryGate and policy-packing can be scored on ASR.

### 2.4 SREGym — the gold-standard live SRE benchmark

- **Paper:** *SREGym: A Live Benchmark for AI SRE Agents with High-Fidelity Failure Scenarios* (Clark et al., 2026). arXiv 2605.07161.
- **Repo:** `github.com/SREGym/SREGym`
- **What it tests:** 90 SRE problems across application, platform, OS, and hardware layers. Supports live Kubernetes environments and verifies actual mitigation.
- **Metrics:** diagnosis success, mitigation success, efficiency.
- **Why it fits:** It is the most realistic incident-response benchmark. It directly addresses the criticism that static benchmarks miss iterative, time-ordered troubleshooting.
- **Caveat:** Requires a live Kubernetes cluster and tooling (Alibaba Cloud ACK). Not a quick local drop-in.

### 2.5 ITBench — IBM's open IT automation benchmark

- **Repo:** `github.com/itbench-hub/ITBench`
- **What it tests:** SRE, CISO, and FinOps tasks on Kubernetes/OpenShift. 6 SRE scenarios and 21 fault mechanisms.
- **Why it fits:** Established, open-source, real-world incident scenarios. Heavier than our fixture-based approach but more credible than hand-written JSON.

### 2.6 AIOpsLab / AIOps2025 / RCA100 — microservice failure diagnosis

- **Paper:** *A Multi-Dataset Benchmark for Evaluating LLM Agents in Microservice Failure Diagnosis* (arXiv 2606.29193, 2026).
- **What it tests:** 400-case `AIOps2025` and 103-case `RCA100` with causal-chain labels and multimodal signals.
- **Why it fits:** Good for root-cause diagnosis benchmarking if we expand beyond remediation into RCA.

## 3. Recommended benchmark roadmap

| Priority | Benchmark | Effort | Value for TriageTrace |
|---|---|---|---|
| P0 | **Keep custom adversarial suite + reframe it** | low | Current hackathon demo; proves domain-specific firewall behaviors |
| P1 | **MemoryAgentBench subset** | medium | Best public match for the MemoryAgent track; gives an externally comparable number |
| P1 | **ASB memory-poisoning subset** | medium | Validates adversarial security claims with ASR/RR |
| P2 | **ITBench SRE scenarios** | high | Real K8s incidents; strong for production credibility |
| P2 | **SREGym** | high | Live, high-fidelity SRE failures; best long-term validation |
| P3 | **AIOps2025 / RCA100** | high | If we pivot toward root-cause analysis as a feature |

## 4. Concrete next steps

1. ✅ **Reframe the README benchmark section** so the 13-scenario suite is presented as a *domain-specific adversarial smoke test* inspired by MemoryAgentBench categories and ASB memory-poisoning cases.
2. ✅ **Add a `backend/evaluations/benchmarks/memoryagentbench.py` runner** that downloads the public `Conflict_Resolution` subset and runs it through memory retrieval. Report CR and selective-forgetting scores.
3. ✅ **Add an ASB poison subset runner** that tests MemoryGate against ASB tool descriptions and reports quarantine metrics.
4. **Scale the runners**: run MemoryAgentBench on more samples/splits (AR, TTL) and ASB on the full 400-attack set; add per-tool F1 and attack-category breakdowns.
5. **Close the retrieval gap**: add multi-hop / query-expansion retrieval so the top-k recalled facts contain the full chain needed for CR questions.
6. **Document the ITBench/SREGym gap** in `docs/benchmark_strategy.md` as future work requiring live Kubernetes on Alibaba Cloud ACK.

## 5. Suggested wording for the README / Devpost page

> TriageTrace is evaluated on a 13-scenario adversarial smoke test, a public AgentSecurityBench (ASB) memory-poisoning gate, and a pilot MemoryAgentBench conflict-resolution subset. The smoke test is intentionally compact and can be run live against Qwen Cloud in minutes; the public benchmarks provide externally comparable ASR/RR and accuracy numbers. We are working toward scaling the MemoryAgentBench and ASB runners to full splits, and to ITBench/SREGym for live Kubernetes incident validation.

## 6. Sources

- Hu et al., "Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions" (MemoryAgentBench), arXiv 2507.05257 (2025).
- Tan et al., "MemBench: Towards More Comprehensive Evaluation on the Memory of LLM-based Agents," ACL Findings 2025 / arXiv 2506.21605.
- Zhang et al., "Agent Security Bench (ASB): Formalizing and Benchmarking Attacks and Defenses in LLM-based Agents," ICLR 2025.
- Clark et al., "SREGym: A Live Benchmark for AI SRE Agents with High-Fidelity Failure Scenarios," arXiv 2605.07161 (2026).
- Jha et al., "ITBench: Evaluating AI Agents across Diverse Real-World IT Automation Tasks" (OpenReview / IBM, 2025).
- AIOps2025 / RCA100 paper, "A Multi-Dataset Benchmark for Evaluating LLM Agents in Microservice Failure Diagnosis," arXiv 2606.29193 (2026).
- Datadog, "How we built an AI SRE agent that investigates like a team of engineers" (2026).
- Microsoft, "AI Under Attack: A Defender's Guide to Memory Poisoning" (2026).
