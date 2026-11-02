"""find_object: where is the <colour> object and how big is it? (camera only, no motion)"""
from robot_plugins.vision_tools import find_objects

from robot_plugins import PluginBase


class Plugin(PluginBase):
    def call(self, tool, args):
        frame = self.ctx.camera.latest_frame()
        if frame is None:
            return {'ok': False, 'error': 'kamera karesi yok (simülasyon/kamera çalışıyor mu?)'}
        color = args.get('color', 'any')
        objects = find_objects(frame, None if color == 'any' else [color],
                               self.ctx.params['max_aperture'])
        return {'ok': True, 'found': bool(objects), 'count': len(objects), 'objects': objects}
