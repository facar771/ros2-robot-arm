"""A real Robot API v1 server (WebView + JobManager + Supervisor, robot_core) over fake skills, so
the agent tests exercise the actual HTTP contract without ROS."""
import threading

from robot_agent.client import HttpRobot
from robot_core.jobs import JobManager
from robot_core.safezone import SafetyConfig, SafeZone
from robot_core.supervisor import CancelFlag, EStop, Supervisor
from robot_core.webcmd import CommandRunner
from robot_core.webview import Snapshot, WebView
from tests.fakes import CancelAwareSkills, FakeArm, FakeTool, RecordingSink

NAMES = ('joint_1', 'joint_2')
BOX = SafeZone((0.05, -0.5, 0.02), (0.75, 0.5, 0.7))


class Source:
    def snapshot(self):
        return Snapshot()


class Rig:
    """Fake skills: ``above`` runs until ``release`` is set, the job is cancelled or e-stopped."""

    def __init__(self, zone=BOX, auto_release=True):
        self.estop, self.cancel, self.sink = EStop(), CancelFlag(), RecordingSink()
        self.skills = CancelAwareSkills(self.cancel, self.estop, steps=1500)
        self.skills.entered = threading.Event()
        self.skills.release = threading.Event()
        if auto_release:
            self.skills.release.set()
        self.arm = FakeArm(NAMES)
        self.runner = CommandRunner(self.skills, FakeTool((0, 0, 1)), self.sink, estop=self.estop)
        self.jobs = JobManager(self.runner, self.cancel, self.estop)
        safety = SafetyConfig(zone, 0.5).as_dict(0.5)
        self.supervisor = Supervisor(self.estop, self.arm, NAMES, stop_port=self.arm,
                                     runner=self.runner, events=self.sink, safety=safety)
        self.view = WebView(Source(), port=0, commands=self.runner, supervisor=self.supervisor,
                            jobs=self.jobs)
        self.view.start_background()
        self.robot = HttpRobot(f'http://127.0.0.1:{self.view.port}')

    def commands_run(self):
        """The robot commands that actually reached the skills layer."""
        return [c for c in self.skills.calls]

    def request_text(self, method, path):
        """Raw status + body + headers of one Robot API response (for leak searches)."""
        import http.client
        conn = http.client.HTTPConnection('127.0.0.1', self.view.port, timeout=10)
        try:
            conn.request(method, path)
            resp = conn.getresponse()
            return f'{resp.status} {resp.read().decode()} {dict(resp.getheaders())}'
        finally:
            conn.close()

    def close(self):
        self.skills.release.set()
        self.view.shutdown()
