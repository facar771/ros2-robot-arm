"""scan_extent: pan the camera across the scene and list every coloured object with 3-D measures.

With ``camera:pan`` and a camera that can turn, it sweeps ``yaw_min_deg..yaw_max_deg`` in ``steps``
views and merges them (``robot_vision.scan``). Otherwise it falls back to the current view only
(``views: 1``) and says so, instead of pretending to have scanned the room.
"""
import math

from robot_plugins.vision_tools import describe_measure, find_objects
from robot_vision.geometry import GeometryConfig
from robot_vision.scan import ScanConfig, scan

from robot_plugins import PluginBase, Stopped


class Plugin(PluginBase):
    def call(self, tool, args):
        ctx = self.ctx
        if not ctx.camera.pan.available:
            frame = ctx.camera.latest_frame()
            if frame is None:
                return {'ok': False, 'error': 'kamera karesi yok'}
            objects = find_objects(frame, None, ctx.params['max_aperture'])
            return {'ok': True, 'views': 1, 'swept': False, 'count': len(objects),
                    'objects': objects,
                    'note': 'kamera döndürülemiyor; yalnızca şu anki görüntü tarandı'}
        cfg = ScanConfig(yaw_min=math.radians(args['yaw_min_deg']),
                         yaw_max=math.radians(args['yaw_max_deg']), steps=args['steps'],
                         merge_distance=ctx.params['merge_distance_m'])
        result = scan(ctx.camera.pan, cfg,
                      geometry=GeometryConfig(max_aperture=ctx.params['max_aperture']),
                      should_stop=ctx.stop.is_set)
        if result.stopped:
            raise Stopped()
        objects = [{'color': o.label, 'seen_in_views': o.views, 'partial': o.partial,
                    'measured_at_yaw_deg': round(math.degrees(o.yaw), 1),
                    **describe_measure(o.measure)} for o in result.objects]
        return {'ok': True, 'swept': True, 'views': result.views_used,
                'angles_deg': [round(math.degrees(a), 1) for a in result.angles],
                'count': len(objects), 'objects': objects, 'notes': result.notes}
