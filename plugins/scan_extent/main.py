"""scan_extent: list every coloured object in view with its 3-D measurements (single view)."""
from robot_plugins.vision_tools import find_objects

from robot_plugins import PluginBase


class Plugin(PluginBase):
    def call(self, tool, args):
        frame = self.ctx.camera.latest_frame()
        if frame is None:
            return {'ok': False, 'error': 'kamera karesi yok'}
        objects = find_objects(frame, None, self.ctx.params['max_aperture'])
        return {'ok': True, 'views': 1, 'count': len(objects), 'objects': objects}
