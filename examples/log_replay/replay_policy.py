"""Placeholder policy for log replay: the recorded robot already acted."""


class RecordedPolicy:
    def act(self, observation):
        raise RuntimeError("log replay does not run a policy")
