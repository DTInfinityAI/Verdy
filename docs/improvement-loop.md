# Improvement loop: from testing robot AI to improving it

Every Verdy test run produces more than a verdict. It shows where a policy is fragile,
by how much it nearly failed, and under which conditions. The improvement loop turns that
information into training signal, and then proves every improvement with a fresh,
independent verdict.

```
        ┌──────────────────────────────────────────────────────────────────┐
        │                                                                  │
        ▼                                                                  │
   ┌─────────┐   ┌──────────────┐   ┌───────────────┐   ┌───────┐   ┌──────┴──────┐
   │ Diagnose│──▶│ Rewards and  │──▶│ Failure map & │──▶│ Train │──▶│ Re-certify  │
   │ (test)  │   │ human feedback│  │ curriculum    │   │       │   │ (held out)  │
   └─────────┘   └──────────────┘   └───────────────┘   └───────┘   └─────────────┘
        ▲                                                        promote only if
        └── expert demonstrations give the starting point        proven no worse
```

| Capability | How Verdy does it | Module |
| --- | --- | --- |
| **Safety margins as rewards** | STL robustness becomes a reward that keeps paying until a run clears each spec by a margin, so the policy learns to keep its distance from failure instead of scraping a pass. | `SafetyMarginReward` |
| **Targeted practice** | A failure map shows which conditions fail most; a curriculum draws most training scenarios near observed failures. | `failure_map`, `FailureFocusedCurriculum` |
| **Human feedback (RLHF)** | Operators compare pairs of test runs and pick the better one. A reward model learns what they value: smoothness, caution, "how an expert would do it". | `FileLabeler`, `BradleyTerryRewardModel`, `PreferenceReward` |
| **Learning from experts** | Recorded operator sessions pretrain the policy before feedback-driven training. | `record_demonstrations`, `Trainer.pretrain` |
| **Proven, not assumed** | Each candidate and the current policy are certified on fresh scenarios from a separate seed stream that training never sees. The candidate is promoted only if it does at least as well, without regressing guarded specs. | `ImprovementLoop` |

## Quick start

```bash
cd examples/home_robot
verdy improve improve.yaml
```

```text
Starting policy: FAIL  p_fail=0.114 [0.09783, 0.1319]
After pretraining on demonstrations: INCONCLUSIVE  p_fail=0.062 [0.04993, 0.07603]
Cycle 1: candidate INCONCLUSIVE  p_fail=0.043 [0.03296, 0.0551] vs incumbent 0.054 -> promoted (failure probability on held-out scenarios is no worse)
Cycle 2: candidate PASS  p_fail=0.029 [0.02082, 0.03933] vs incumbent 0.047 -> kept incumbent (reach_goal regressed from 0.001 to 0.013 (tolerance 0.01))
Cycle 3: candidate PASS  p_fail=0.011 [0.006181, 0.01814] vs incumbent 0.047 -> promoted (verdict improved to PASS)
Final certified policy: PASS  p_fail=0.011 (upper 0.01814)
  per-spec violation rates: no_collision 0.003, slow_near_person 0.01, reach_goal 0.007
```

The demo takes about a minute and a half on the built-in simulator. The baseline
navigator from `run.yaml` goes from 11.4% failures (`FAIL`) to 1.1% (`PASS`) on held-out
scenarios. Cycle 2's candidate was safer, but it was rejected because it reached the goal
less often than the guard allowed. The "operator" in this demo is simulated
(`simulated_operator.py`). With real operators, use the file labeler and real
recordings.

## One cycle in detail

1. **Diagnose.** Evaluate the current policy on a fresh diagnosis sample of the ODD
   (`diagnose`), keeping traces.
2. **Reward.** Score every run: `safety.weight × SafetyMarginReward +
   preference.weight × PreferenceReward`.
3. **Feedback.** Pick the most informative pairs (where the reward model is least sure,
   or, before one exists, where safety scores are closest), send them to the labeler, and
   refit the reward model on all preferences collected so far
   (`<output>/preferences.jsonl`).
4. **Target.** Build the failure map and fit the curriculum to the runs that failed or
   came closest to failing. Draw `curriculum.scenarios` training scenarios, `focus` of
   them near failures and the rest from the nominal ODD.
5. **Train.** The trainer gets everything (`TrainingData`) and a `TrainingContext` to
   roll out policies on training scenarios and score them with this cycle's reward.
6. **Re-certify.** Evaluate the candidate and the incumbent on the same fresh
   certification sample. Certification seeds come from their own stream
   (`certify.seed + 1000 × cycle`), and any training scenario that coincides with a
   certification scenario is dropped. Promote according to `promote` and `guard`.

Before cycle 1, the starting policy is certified, pretrained on demonstrations (if
there are any and the trainer supports it), and certified again. That shows what the
experts' sessions contributed on their own.

## Rewards

### Safety margins

```yaml
reward:
  safety: {weight: 1.0, margin: 0.3, violation_penalty: 3.0, weights: {minor: 0.6}}
```

For each spec, the score is `min(robustness, margin) / margin`, floored at -1, minus
`violation_penalty` when the spec is violated. Specs are averaged with weights by
severity (default: critical 1, major 0.5, minor 0.2). A run that clears every spec by
`margin` scores 1. A run that only just passes scores close to 0. `margin` may also be a
dict per spec, in the spec's own units.

Choose `violation_penalty` so that one failure costs more than any gain in style:
certification counts failures, so training should too. Give task-completion specs enough
weight that the cheapest way to be safe is not to stop doing the task.

### Preferences

The preference reward is the reward model's prediction, standardized against the current
diagnosis runs and squashed with `tanh` into (-1, 1). A learned reward is unbounded, and
an optimizer will exploit it (reward hacking). Bounding it lets operator preferences shape
*how* the robot behaves without ever outweighing a safety violation.

## Human feedback

### Collecting preferences from operators

```yaml
feedback:
  labeler: {type: file, directory: feedback}
  pairs_per_cycle: 30
```

Each cycle writes pairs to `feedback/pending.jsonl`. Every line holds both runs'
parameters, safety scores, features and the path of their saved traces, ready for a
review tool, a notebook or a spreadsheet. Operators append answers to
`feedback/labels.jsonl`:

```json
{"pair_id": "c1-s00012~s00187", "choice": "a", "reason": "brakes earlier and smoother", "labeler": "maria"}
```

`choice` is `a`, `b` or `tie`. Answers can come in later: the loop uses whatever labels
exist each time it runs, and keeps all preferences in `<output>/preferences.jsonl`.
Other labelers: `{type: cli}` asks in the terminal, `{type: scripted, function:
module:fn}` calls a function (simulations and tests only), and `{type: module:Class}`
plugs in your own, for example a web app or a labeling service.

### The reward model

`BradleyTerryRewardModel` fits a linear reward over run features so that
`P(a preferred over b) = sigmoid(r(a) - r(b))`. Features are each spec's robustness plus,
for every trace signal, its minimum, maximum, mean and roughness (mean absolute rate of
change, a smoothness measure). The loop report lists the learned weights with the most
influential first, so you can see what operators actually value. Replace it with any
object that has `fit(preferences)`, `predict(features)` and `fitted`:

```yaml
reward_model: {type: my_models:NeuralRewardModel, options: {hidden: 64}}
```

## Learning from experts

```yaml
demonstrations: [demos/operator.jsonl]
```

Demonstrations are JSON Lines, one control step per line with `obs` and `action` (plus
optional `session` and `scenario`). Export them from teleoperation logs, or record an
expert or a teleop bridge on a backend:

```python
from verdy.improve import record_demonstrations
record_demonstrations(odd, backend, expert_policy, scenarios, "demos/operator.jsonl")
```

Trainers with a `pretrain(policy, demonstrations, ctx)` method use them before cycle 1.
`ParameterSearchTrainer` does behavior cloning: it fits the policy's parameters to
minimize the squared error between its actions and the expert's.

## Plugging in a trainer

The trainer is the RL or RLHF module. Anything with `train(policy, data, ctx) -> policy`
works.

### Built in: parameter search

```yaml
trainer:
  type: parameter_search
  options:
    bounds: {cruise: [0.5, 1.2], slow_radius: [0.8, 3.5]}
    iterations: 3
    population: 10
policy:
  factory: policy:make_policy
  params: {cruise: 1.0, slow_radius: 1.5}
```

Cross-entropy search over the parameters of `factory(**params)`, maximizing the cycle's
reward on the curriculum. It needs no ML framework and suits controllers with tunable
parameters.

### Any framework, as an external command

```yaml
trainer:
  type: command
  options:
    command: python train_ppo.py --data {data_dir} --out {out_dir} --init {policy_in}
    loader: my_policies:load_checkpoint        # loader(out_dir) -> policy
    secrets: [WANDB_API_KEY]                    # names only; passed to the command
    extra_env: {CUDA_VISIBLE_DEVICES: "0"}
    timeout: 86400
```

Each cycle, Verdy exports the data to `<output>/training/cycle_<n>/data`:

| File | Contents |
| --- | --- |
| `episodes.jsonl` | Diagnosis runs: params, seed, robustness, features, reward |
| `traces/<id>.json` | Their traces |
| `preferences.jsonl` | Every preference collected so far |
| `reward_model.json` | Reward model summary |
| `scenarios.jsonl` | The training curriculum (params and seeds) |
| `demonstrations.jsonl` | Expert steps, if any |
| `manifest.json` | Cycle, file list, input policy |

Then it runs the command and loads the policy that `loader` returns. This is how to plug
in TRL, Stable-Baselines3, LeRobot, an RLHF pipeline on a GPU cluster, or in-house code.
The command gets a minimal environment: basic system variables, the listed `secrets`
(see [credentials](secrets.md)) and `extra_env`. Its output is redacted into `train.log`.

### Any framework, in Python

```yaml
trainer: {type: my_rl:PPOTrainer, options: {learning_rate: 3.0e-4}}
```

```python
class PPOTrainer:
    def __init__(self, learning_rate: float): ...

    def pretrain(self, policy, demonstrations, ctx):     # optional
        ...                                              # behavior cloning
        return policy

    def train(self, policy, data, ctx):
        # data.scenarios: where to practice;  ctx.rollout(policy, scenarios) -> episodes
        # ctx.reward(episode) -> float;       data.preferences, data.reward_model, ...
        ...
        return improved_policy
```

`ctx.rollout` runs on the evaluation backend (simulator, SceneSmith scenes, ...), so the
trainer can do on-policy RL without writing a simulator integration. Training never sees
certification scenarios.

## Certification and promotion

```yaml
diagnose: {runs: 300, sampler: stratified, seed: 100}
certify:  {runs: 1000, sampler: stratified, seed: 9000}
promote: no_worse          # or: better
guard: {reach_goal: 0.01}
```

* Diagnosis and certification must use different seeds. Each cycle certifies on a fresh
  sample, so a policy cannot be tuned to one fixed test set.
* Candidate and incumbent are judged on the same sample (a paired comparison), so luck
  in the sample does not decide promotion.
* `no_worse` promotes when the verdict is not worse and the failure probability is not
  higher; `better` requires a strictly lower failure probability.
* `guard` lists specs whose violation rate may not rise by more than a tolerance. Use it
  for specs that do not decide the verdict, such as task completion, so safety is not
  bought by making the robot useless.

`verdy improve` exits with the final certified verdict (0 `PASS`, 1 `FAIL`, 3
`INCONCLUSIVE`), so it can gate a training pipeline.

## Outputs and the evidence trail

```
<output>/
├── loop.report.json                    sealed summary of every cycle (sign with --sign)
├── preferences.jsonl                   all human preferences
├── cycle_0/certification*.report.json  starting policy (before and after pretraining)
├── cycle_<n>/diagnosis.report.json
├── cycle_<n>/certification_candidate.report.json
├── cycle_<n>/certification_incumbent.report.json
└── training/cycle_<n>/                 exported data and logs (command trainer)
```

Every evaluation is a normal Verdy evidence report. The loop report records the starting
policy, pretraining results, reward configuration, the reward model's learned weights,
the curriculum, the weakest cells of each failure map, training statistics, and both
certifications with their report digests, so every claimed improvement can be traced to
its evidence.

## Limits

* A loop improves the policy against *these* specs and *this* ODD. Requirements that are
  not written as specs are not optimized and are not checked.
* Results from a simulator are evidence about that simulator. Re-certify on recorded logs,
  SceneSmith scenes or hardware before deployment.
* Preferences are only as good as the people giving them. Look at the reward model's
  weights in the loop report to see what it learned.
* Repeatedly certifying on fresh samples keeps each verdict honest. Running many cycles
  and picking the best one adds some selection optimism, so for a release decision run a
  final, separate `verdy run` on a new seed.
