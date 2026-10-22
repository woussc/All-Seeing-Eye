# LangAlpha comparison and recommended direction

Prepared 22 October 2026, Singapore time. Source reviewed at [`29baaf16ef348427e646f785e0a156ad8e399343`](https://github.com/ginlix-ai/LangAlpha/tree/29baaf16ef348427e646f785e0a156ad8e399343). This was a read-only source review, not an installation, runtime test, security certification or investment-performance evaluation.

## Recommendation

Evolve our monitor into a **cross-asset research agent with reproducible studies and persistent hypotheses**. LangAlpha is ahead on general agent infrastructure. Borrow its interfaces and context-management patterns; consider it as an optional future local runtime rather than rebuilding its entire orchestration stack.

Retain the existing constraints: $0 incremental cost, no new accounts, public machine-readable data, twelve core references, and research before position management. The original workflow remains macro → measured flows → positioning → price confirmation → interpretation → three research priorities. A trading-agent comparison does not establish a validated trading edge or change this scope.

## What LangAlpha actually provides

| Area | Reviewed LangAlpha implementation | Our current position |
| --- | --- | --- |
| Agent runtime | Model abstraction, Python/Bash tools, parallel steerable subagents and bounded QuickJS workflows | Deterministic monitor and reports; no deployed agent runtime |
| Memory | Persistent workspaces, user/workspace memory, transcripts and context compaction | Frozen snapshots/reports, browser journal and development recovery checkpoints |
| Evidence | Per-turn source records, redacted arguments, fingerprints and captured result bodies | Source timestamps, report digests and validated publication generations; no per-agent claim ledger |
| Automation | Schedules and price-triggered agents on a running server | Daily GitHub workflow; best-effort scheduling, not an always-on agent |
| Data | Keyless Yahoo/SEC baseline plus optional hosted/keyed providers | Public price/Treasury adapters; flows, positioning and several macro sources remain incomplete |
| Execution | Remote broker connectors and governed order dispatch/reconciliation | Research-only scope; no broker integration |
| Backtesting | Agent-written code can run studies; no packaged reproducible backtesting service found in the targeted review | No backtest engine; an opportunity to add a controlled study layer |
| Deployment | Backend, PostgreSQL, Redis and a compute/sandbox runtime | Static GitHub Pages UI plus scheduled Python collection |

Source anchors: [agent construction](https://github.com/ginlix-ai/LangAlpha/blob/29baaf16ef348427e646f785e0a156ad8e399343/src/ptc_agent/agent/agent.py#L1057), [checkpoint persistence](https://github.com/ginlix-ai/LangAlpha/blob/29baaf16ef348427e646f785e0a156ad8e399343/src/server/utils/checkpointer.py#L95), [provenance records](https://github.com/ginlix-ai/LangAlpha/blob/29baaf16ef348427e646f785e0a156ad8e399343/src/ptc_agent/agent/provenance/types.py#L28), [provider configuration](https://github.com/ginlix-ai/LangAlpha/blob/29baaf16ef348427e646f785e0a156ad8e399343/config.yaml#L200), and [hosted versus self-hosted requirements](https://github.com/ginlix-ai/LangAlpha/blob/29baaf16ef348427e646f785e0a156ad8e399343/README.md#L185).

Important qualifications:

- It already has provenance and persistent memory. Those alone would not differentiate our system.
- A source hash establishes which bytes were recorded, not whether a financial conclusion follows. Sandbox traces are explicitly treated as untrusted in its [capture middleware](https://github.com/ginlix-ai/LangAlpha/blob/29baaf16ef348427e646f785e0a156ad8e399343/src/ptc_agent/agent/middleware/provenance/middleware.py#L126).
- Its moomoo paper connector uses a remote broker simulator. The adapter describes full fills at reference prices without realistic depth/partial-fill modeling; this is not a historical execution simulator ([adapter](https://github.com/ginlix-ai/LangAlpha/blob/29baaf16ef348427e646f785e0a156ad8e399343/src/server/services/brokerage_orders/moomoo.py#L909)).
- The review found no finance-specific answer-quality or profitability benchmark establishing superior investment results. Our proposed differentiation is an engineering goal, not demonstrated outperformance.
- The source is [Apache 2.0](https://github.com/ginlix-ai/LangAlpha/blob/29baaf16ef348427e646f785e0a156ad8e399343/LICENSE). Any later code reuse should preserve the applicable license and notices. No code was copied in this review.

## Improvements that fit our goals

### 1. Complete the monitoring foundation

First confirm the existing Pages deployment and publish a known revision. Finish authoritative request reservations, split quote refreshes from daily history, and verify source access/limits before activating faster collection. Add one release/positioning/flow source at a time, preserving its actual scope and publication cadence.

This addresses the present bottleneck: the latest governed test capture had twelve unavailable sources, and the upgrade exists only on a feature branch. Adding agent conversation would not resolve that evidence gap.

Acceptance: a verifiable deployment URL/revision; successful dated records for each enabled adapter; durable budgets surviving cancellation/restarts; honest stale/unavailable states; daily indicators tied to verified completed sessions.

### 2. Add a research workspace and evidence pack

Each of the three priorities becomes a persistent hypothesis with supporting records, contrary evidence, an invalidation condition, next review catalyst, change history and eventual outcome. Label observed facts and explanations separately. Do not present uncalibrated model conviction as a measured probability.

Add a downloadable compact evidence pack containing the selected snapshot ID, coverage/freshness matrix, source references, computed metrics, open questions and current hypotheses. The user can use it in an existing ChatGPT conversation and save the resulting research back to the journal. Keep personal notes browser-local/exported unless a separate private storage capability is chosen.

Acceptance: a reopened hypothesis preserves its original evidence; every numerical claim references an artifact; missing flows stay missing; changed source releases produce an explicit revision rather than rewriting history.

### 3. Add reproducible studies before more agent autonomy

Build a small deterministic engine for explicit strategy specifications, beginning with classic momentum or a trend filter across supported instruments. Record dataset/version hashes, code/config version, actual eligible start, warmup, benchmark, rebalance timing, next-bar execution, dividends/adjustment basis, turnover and stated costs/slippage. Distinguish in-sample exploration, locked holdout results and later observations; log all parameter trials.

Start with predefined, validated rules and bounded jobs. Unrestricted agent-generated scripts should not become privileged CI jobs. Historical execution and corporate-action assumptions must be disclosed; paper fills are a separate test.

A requested 1990 start cannot be satisfied by this exact ETF/crypto universe: many instruments did not exist. A study must fail clearly, start at supported coverage, or use separately identified historical proxies with explicit basis differences. Do not fabricate or splice an undeclared 1990 history. A selected modern universe also needs its selection limitations stated.

Acceptance: the same data/config produces the same output; look-ahead and missing-data tests fail the run; results include costs, drawdown, turnover and holdout evidence; an unsupported start returns a structured reason. No winning study is guaranteed.

### 4. Expose a narrow research-tool interface

Define model-independent functions such as `describe_capabilities`, `get_snapshot`, `get_evidence`, `compare_snapshots`, `run_study` and `get_study_result`. They return typed facts and artifact references, with explicit unavailable/unsupported states. Keep large price tables in files/computation; only bounded summaries enter model context.

The initial bridge is the exported evidence pack. A later local SDK/CLI or read-only MCP adapter can connect the same core to a suitable agent runtime, including LangAlpha. Tool names here are proposed, not currently callable.

GitHub Pages cannot host a Python agent, a dynamic MCP endpoint or continuous model inference. Remote access requires a separately available runtime. Under the current constraints, retain scheduled deterministic work on GitHub and on-demand analysis in the existing chat; local inference is an option only if suitable existing hardware is available. A ChatGPT subscription does not imply a free application API allowance.

### 5. Use selective agents and measure their value

When a runtime is justified, start with one coordinator and at most two specialists for independent research. Reuse deterministic calculations and validation across all roles. Dispatch only for changed evidence or an explicit user question; no model calls for idle quote refreshes.

Adopt LangAlpha's code-first data processing, progressive tool discovery, scoped memory and checkpoint pointers. Keep raw data outside prompts. Each run should record its objective, evidence generation, artifacts, completed steps, unresolved questions, costs and exact next action. Compacted chat history is not the authoritative research record.

Evaluate agents on fixed snapshots/questions using citation coverage, numerical accuracy, abstention on missing inputs, reproducibility, unsupported causal claims, token usage and runtime. Compare alternatives on identical inputs; do not infer quality from model size or agent count.

## Execution order and scope

1. Finish publication and data reliability work already recorded in `freshness-checkpoint.md`.
2. Implement hypothesis records and evidence-pack export using the existing journal/UI.
3. Add one reproducible study path with explicit failure cases, then the research-tool contract.
4. Evaluate a local LangAlpha integration only when runtime/hardware/model access fits the constraints. Reuse its harness if it saves maintenance; keep our data/metrics/study core independently usable.

Broker execution, account integration and autonomous order management are a separate later product decision. This comparison introduces no account, model call, broker connection, faster collection schedule or deployment change.
