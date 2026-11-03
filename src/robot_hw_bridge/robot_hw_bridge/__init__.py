"""ESP32 donanım hattı (B1): ROS'suz mesaj kodeği, ESP32 simülatörü, host tarafı bağlantı.

Belge: docs/HARDWARE.md. B2 (ROS düğümü) ve B3 (firmware) bu paketin üstüne gelir.
"""
from robot_hw_bridge.codec import Frame, FrameDecoder, SeqTracker, encode_frame
from robot_hw_bridge.crc import crc16
from robot_hw_bridge.host import HwLink, LinkMonitor
from robot_hw_bridge.protocol import MsgType, State
from robot_hw_bridge.sim import Esp32Sim, SimAxis
from robot_hw_bridge.transport import LoopbackLink, SimRunner, SimServer, SocketTransport

__version__ = '0.1.0'
__all__ = ['Esp32Sim', 'Frame', 'FrameDecoder', 'HwLink', 'LinkMonitor', 'LoopbackLink', 'MsgType',
           'SeqTracker', 'SimAxis', 'SimRunner', 'SimServer', 'SocketTransport', 'State', 'crc16',
           'encode_frame']
