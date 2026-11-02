"""follow_color: keep the tool above a coloured object that may move.

Camera -> object position; whenever it moved more than ``min_move_m`` since the last job, a new
``above <colour> <height>`` job goes through the Robot API (safe box, speed cap, e-stop all apply).
The plugin never moves anything itself and gives up at once on an emergency stop (423): a latched
stop is for a human to reset, not for a plugin to retry.
"""
import math
import time

from robot_plugins.robot_api import RobotBusy, RobotLocked, RobotRefused
from robot_plugins.vision_tools import find_objects

from robot_plugins import PluginBase


class Plugin(PluginBase):
    def call(self, tool, args):
        ctx, color = self.ctx, args['color']
        height, min_move = args['height_m'], args['min_move_m']
        deadline = time.monotonic() + args['duration_s']
        poll = ctx.params['poll_s']
        last_target, jobs, lost = None, 0, 0

        def finish(reason, **extra):
            return {'ok': reason in ('duration', 'stopped'), 'reason': reason, 'jobs': jobs,
                    'last_target': last_target, **extra}

        ctx.camera.latest_frame()          # wait for the first frame once (live camera start-up)
        while time.monotonic() < deadline:
            if ctx.stop.is_set():
                return finish('stopped')
            frame = ctx.camera.latest_frame(wait_s=0)    # the loop handles None itself
            found = find_objects(frame, [color]) if frame is not None else []
            if not found:
                lost += 1
                if lost * poll > 5.0:
                    return finish('not_found', error=f'{color} nesne 5 s boyunca görünmedi')
                ctx.stop.wait(poll)
                continue
            lost = 0
            pos = found[0]['top_center']
            moved = last_target is None or math.dist(pos, last_target) > min_move
            if moved:
                try:
                    job = ctx.robot.submit_job(f'above {color} {height:.2f}')
                except RobotLocked:
                    return finish('estop', error='robot acil dur durumunda; plugin yeniden denemez')
                except RobotBusy:
                    ctx.stop.wait(poll)                      # a previous job still runs
                    continue
                except RobotRefused as exc:
                    return finish('refused', error=exc.message)
                last_target = pos
                jobs += 1
                ctx.log(f'iş {job["id"]}: above {color} {height:.2f}')
                done = ctx.robot.wait_for_job(job['id'], ctx.stop, timeout=120.0)
                if done.get('state') == 'cancelled' and done.get('cancelled_by') == 'estop':
                    return finish('estop', error='iş acil dur ile iptal edildi')
                if done.get('state') == 'failed':
                    return finish('refused', error=done.get('error') or 'iş başarısız')
            ctx.stop.wait(poll)
        return finish('duration')
