# Verdy documentation

| Guide | What it covers |
| --- | --- |
| [Getting started](getting-started.md) | Install, run the example, evaluate your own policy |
| [ODD specification](odd-spec.md) | The ODD document format (v0.4.0): parameters, distributions, constraints, provenance |
| [Ontology and resolvers](ontology.md) | The ontology tree and its rendered LLM skill; resolving descriptions with `exact` / `laya` / `laya-tree` / `llm`; provenance; the approval log |
| [Laya fine-tuning](laya-finetuning.md) | Training the Laya tree walker on your approvals: dataset, items, training, per-level evaluation, promotion, retraining |
| [Safety specs (STL)](stl-specs.md) | Writing STL requirements and how robustness is scored |
| [Scenario sampling](sampling.md) | Monte Carlo, stratified, importance sampling, log replay |
| [Execution backends](backends.md) | Built-in simulator, log replay, SceneSmith, custom simulators and HIL |
| [Verdicts and statistics](verdicts.md) | The PASS / FAIL / INCONCLUSIVE rule, bounds, coverage, and their limits |
| [Evidence ledger](evidence-ledger.md) | Report contents, digests, signing, reproducing runs |
| [Runtime monitors](runtime-monitors.md) | Running the same specs on the robot |
| [Improvement loop](improvement-loop.md) | From testing to improving: safety-margin rewards, targeted practice, RLHF, expert demonstrations, plug-in trainers, re-certification |
| [Evidence store and history](evidence-store.md) | Content-addressed Parquet traces, the DuckDB index, `verdy history` and regression tracking |
| [Credentials and API keys](secrets.md) | Setting keys safely, fingerprints in reports, leak scanning |
| [Command-line reference](cli.md) | Every `verdy` command, option and exit code |
