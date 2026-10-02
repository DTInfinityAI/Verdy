"""From testing robot AI to improving it: a closed, plug-and-play training loop.

Test, find weaknesses, train on them, re-certify. See :mod:`verdy.improve.loop` and
``docs/improvement-loop.md``.
"""
from verdy.improve.curriculum import FailureFocusedCurriculum, failure_map
from verdy.improve.demos import RecordingPolicy, load_demonstrations, record_demonstrations
from verdy.improve.episodes import Episode, episodes_from, rollout, run_features
from verdy.improve.loop import CertificationResult, ImprovementLoop, LoopResult, SplitConfig
from verdy.improve.preferences import (
    BradleyTerryRewardModel,
    CLILabeler,
    FileLabeler,
    Preference,
    ScriptedLabeler,
    load_preferences,
    save_preferences,
    select_pairs,
)
from verdy.improve.rewards import CompositeReward, PreferenceReward, SafetyMarginReward
from verdy.improve.trainers import (
    CommandTrainer,
    ParameterizedPolicy,
    ParameterSearchTrainer,
    TrainingContext,
    TrainingData,
)

__all__ = [
    "BradleyTerryRewardModel",
    "CLILabeler",
    "CertificationResult",
    "CommandTrainer",
    "CompositeReward",
    "Episode",
    "FailureFocusedCurriculum",
    "FileLabeler",
    "ImprovementLoop",
    "LoopResult",
    "ParameterSearchTrainer",
    "ParameterizedPolicy",
    "Preference",
    "PreferenceReward",
    "RecordingPolicy",
    "SafetyMarginReward",
    "ScriptedLabeler",
    "SplitConfig",
    "TrainingContext",
    "TrainingData",
    "episodes_from",
    "failure_map",
    "load_demonstrations",
    "load_preferences",
    "record_demonstrations",
    "rollout",
    "run_features",
    "save_preferences",
    "select_pairs",
]
