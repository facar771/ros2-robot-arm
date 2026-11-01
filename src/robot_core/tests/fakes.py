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


class FakeTool:
    """ToolPositionPort: returns the queued positions in order (the last one repeats); an item
    that is a callable is called, so a test can derive the position from what was commanded."""

    def __init__(self, *positions):
        self.positions = list(positions)
        self.calls = 0

    def tool_position(self):
        self.calls += 1
        item = self.positions[0] if len(self.positions) == 1 else self.positions.pop(0)
        return item() if callable(item) else item


class FakeSkills:
    """Skills stand-in for the web/scenario tests: records calls, can fail, raise or block."""

    def __init__(self, objects=None, fail=None, raises=None, block=None):
        from robot_core.models import ObjectInfo
        self.objects = objects if objects is not None else [
            ObjectInfo('obj_red_1', 'red', (0.35, 0.15, 0.03), (0.06, 0.06, 0.06)),
            ObjectInfo('obj_green_1', 'green', (0.40, -0.10, 0.05), (0.06, 0.06, 0.10))]
        self.fail = fail            # message -> every skill fails with it
        self.raises = raises
        self.block = block          # threading.Event: go_above waits until it is set
        self.entered = None         # threading.Event: set when go_above has started
        self.calls = []

    def _result(self, skill, data=None):
        from robot_core.models import SkillResult
        if self.raises:
            raise self.raises
        if self.fail:
            return SkillResult(False, skill, self.fail, 'FAILED')
        return SkillResult(True, skill, 'hedefe ulaşıldı', 'SUCCESS', data or {})

    def list_objects(self):
        self.calls.append(('list_objects',))
        return list(self.objects)

    def go_to_named_pose(self, name):
        self.calls.append(('go_to_named_pose', name))
        return self._result('go_to_named_pose', {'pose': name})

    def go_above(self, object_name, height=0.10):
        self.calls.append(('go_above', object_name, height))
        if self.entered is not None:
            self.entered.set()
        if self.block is not None:
            self.block.wait(10)
        obj = next((o for o in self.objects if o.label == object_name or o.name == object_name),
                   None)
        if obj is None:
            from robot_core.models import SkillResult
            return SkillResult(False, 'go_above', f"bilinmeyen nesne '{object_name}'")
        target = [obj.position[0], obj.position[1], obj.top_z + height]
        return self._result('go_above', {'object': obj.name, 'target': target})
