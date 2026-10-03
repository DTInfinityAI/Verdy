"""Fine-tune Laya on your approvals, evaluate it, and gate its promotion.

The workflow (see ``docs/laya-finetuning.md``):

1. ``verdy ontology log`` records each human-approved resolution as phrase → leaf
   (:mod:`verdy.odd.approvals`); ``verdy ontology paraphrase`` adds synthetic variants.
2. ``verdy laya dataset`` turns the log into one example per tree level from the current
   ontology (:mod:`verdy.finetune.dataset`), split into train and held-out sets.
3. ``verdy laya items`` tokenizes the training rows into Laya's training-item format
   (:mod:`verdy.finetune.items`), and Laya's own fine-tuning script trains on them.
4. ``verdy laya eval`` scores the new checkpoint on held-out human approvals, per level, and
   promotes it only if accuracy and calibration beat the current one
   (:mod:`verdy.finetune.evaluate`).
5. ``verdy laya due`` says when enough new approvals have accumulated to retrain.
"""
from verdy.finetune.dataset import LevelExample, export_dataset, level_examples
from verdy.finetune.evaluate import compare_reports, evaluate_rows

__all__ = ["LevelExample", "compare_reports", "evaluate_rows", "export_dataset",
           "level_examples"]
