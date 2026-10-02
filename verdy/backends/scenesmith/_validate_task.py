"""Run SceneSmith's task validator and print the result as JSON.

Executed by :class:`verdy.backends.scenesmith.SceneSmithBackend` with SceneSmith's own
Python interpreter (SceneSmith pins its own dependencies), from the SceneSmith checkout.
Mirrors ``scripts/robot_eval/validate.py`` in SceneSmith, but emits machine-readable
output on a single line prefixed with ``VERDY_RESULT``.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene-state", required=True, type=Path)
    parser.add_argument("--dmd", required=True, type=Path)
    parser.add_argument("--scene-dir", required=True, type=Path)
    parser.add_argument("--task", required=True)
    parser.add_argument("--no-vision", action="store_true")
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    sys.path.insert(0, str(Path.cwd()))
    from scenesmith.robot_eval import create_robot_eval_config
    from scenesmith.robot_eval.success_validation.validator_agent import validate_task

    blender_server = None
    if not args.no_vision:
        from scenesmith.agent_utils.blender import BlenderServer

        blender_server = BlenderServer()
        blender_server.start()
    try:
        result = await validate_task(
            task_description=args.task,
            cfg=create_robot_eval_config(model=args.model),
            scene_state_path=args.scene_state,
            dmd_path=args.dmd,
            blender_server=blender_server,
            scene_dir=args.scene_dir,
        )
    finally:
        if blender_server is not None:
            blender_server.stop()

    payload = {
        "overall_success": bool(result.overall_success),
        "overall_score": float(result.overall_score),
        "overall_reasoning": str(getattr(result, "overall_reasoning", "")),
        "requirements": [
            {
                "description": str(r.description),
                "score": float(r.score.to_float()),
                "reasoning": str(r.reasoning),
            }
            for r in result.requirements
        ],
    }
    print("VERDY_RESULT " + json.dumps(payload), flush=True)


if __name__ == "__main__":
    asyncio.run(_main())
