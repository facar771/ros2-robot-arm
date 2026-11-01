"""Test doubles for the ports."""
from robot_core.models import MotionResult


class FakeMotion:
    def __init__(self, result=None):
        self.result = result or MotionResult(ok=True, code=1, code_name='SUCCESS')
        self.targets = []

    def move_to(self, target):
        self.targets.append(target)
        return self.result


class FakeScene:
    def __init__(self, ok=True):
        self.ok = ok
        self.added = []
        self.removed = []

    def add_box(self, obstacle):
        self.added.append(obstacle)
        return self.ok

    def remove_box(self, name):
        self.removed.append(name)
        return self.ok


class FakeState:
    def __init__(self, positions=None):
        self.positions = positions

    def joint_positions(self):
        return self.positions


class RecordingSink:
    def __init__(self):
        self.events = []

    def emit(self, kind, payload):
        self.events.append((kind, payload))


class FakePose:
    def __init__(self, result=None, raises=None):
        self.result = result or MotionResult(ok=True, code=1, code_name='SUCCESS')
        self.raises = raises
        self.targets = []

    def move_to_pose(self, target):
        self.targets.append(target)
        if self.raises:
            raise self.raises
        return self.result


class FakeObjects:
    def __init__(self, objects=None, raises=None):
        self.found = objects
        self.raises = raises

    def objects(self):
        if self.raises:
            raise self.raises
        return self.found
