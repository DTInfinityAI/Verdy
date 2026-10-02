"""Policies for SceneSmith evaluation.

A SceneSmith policy gets the generated scene and writes the final scene: the same Drake
model directives, with objects at the poses the robot left them in. That is SceneSmith's
robot-evaluation contract (see its ``scripts/robot_eval``). Wrap your robot like this:

    class MyRobotPolicy:
        def run(self, scene, output_dmd, seed):
            # scene.dmd        initial .dmd.yaml (load with pydrake Parser; register
            #                  scene.scene_dir as package "scene")
            # scene.state      object metadata (house_state.json)
            # scene.task       the task description
            # scene.prompt     the prompt the scene was generated from
            # scene.work_dir   scratch space for this run
            final_poses = run_my_robot_in_drake(scene, seed)
            write_dmd_with_poses(scene.dmd, final_poses, output_dmd)
            return PolicyOutcome(trace={"time": t, "min_clearance": clearance})

Returning a ``PolicyOutcome`` with a ``trace`` lets STL specs also use signals recorded
while the robot acted (e.g. clearance to people or furniture).
"""
from __future__ import annotations

import shutil
from pathlib import Path


class DoNothingPolicy:
    """Baseline that leaves the scene untouched. Every task should fail; use it to check
    the pipeline end to end before plugging in a real robot."""

    def run(self, scene, output_dmd: Path, seed: int) -> Path:
        shutil.copyfile(scene.dmd, output_dmd)
        return output_dmd
