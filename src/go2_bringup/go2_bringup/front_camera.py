#!/usr/bin/env python3
"""The Go2 front camera as a ROS 2 topic, decoded and re-encoded in hardware.

The Go2 streams its front camera as RTP H.264 over multicast on the internal
network: 230.1.1.1:1720, 1280x720, ~15 fps, 1.4 Mbit/s. That stream is not a
ROS topic, and `/frontvideostream`, the DDS version, cannot be used: the
`Go2FrontVideoData` definition in `unitree_go` does not match the firmware and
deserialisation returns garbage.

This node runs a GStreamer pipeline on the Jetson's hardware blocks

    udpsrc → rtph264depay → nvv4l2decoder → nvvidconv (resize) → nvjpegenc → appsink

and publishes `sensor_msgs/CompressedImage` (JPEG) on
`front_camera/image/compressed`. Every H.264 frame has to be decoded, because
each one depends on the previous ones, so the rate cap applies after decoding:
surplus frames are dropped before publishing.

Measured on 2026-10-07: 640x360 at quality 80 is ~16.7 KB per frame, about
1.3 Mbit/s at 10 Hz, which the Wi-Fi carries through the zenoh bridge (docs/remote-ros.md).

Jetson only (NVIDIA GStreamer elements), with the system Python: OpenCV from
pip has no GStreamer, PyGObject (`gi`) comes from apt.

    ros2 run go2_bringup front_camera --ros-args -p width:=640 -p max_rate:=10.0
"""

import array
import time

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst  # noqa: E402

import rclpy  # noqa: E402
from rclpy.node import Node  # noqa: E402
from rclpy.qos import qos_profile_sensor_data  # noqa: E402
from sensor_msgs.msg import CompressedImage  # noqa: E402

PIPELINE = (
    'udpsrc multicast-group={group} port={port} multicast-iface={iface} '
    'caps="application/x-rtp,media=video,clock-rate=90000,encoding-name=H264,payload=96" '
    '! rtph264depay ! h264parse ! nvv4l2decoder ! nvvidconv '
    '! video/x-raw(memory:NVMM),width={width},height={height},format=I420 '
    '! nvjpegenc quality={quality} '
    '! appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false'
)


class FrontCamera(Node):
    def __init__(self):
        super().__init__('go2_front_camera')
        self.declare_parameter('multicast_group', '230.1.1.1')
        self.declare_parameter('port', 1720)
        self.declare_parameter('iface', 'eth0')
        self.declare_parameter('width', 640)
        self.declare_parameter('height', 360)
        self.declare_parameter('quality', 80)
        self.declare_parameter('max_rate', 10.0)  # 0 = every frame (~15 Hz)
        self.declare_parameter('frame_id', 'front_camera')

        def param(name):
            return self.get_parameter(name).value

        rate = float(param('max_rate'))
        self._min_period = 1.0 / rate if rate > 0 else 0.0
        self._frame_id = param('frame_id')
        self._next_due = 0.0
        self._last_frame = time.monotonic()
        self._published = 0

        self.pub = self.create_publisher(
            CompressedImage, 'front_camera/image/compressed', qos_profile_sensor_data)

        Gst.init(None)
        self.pipeline = Gst.parse_launch(PIPELINE.format(
            group=param('multicast_group'), port=param('port'), iface=param('iface'),
            width=param('width'), height=param('height'), quality=param('quality')))
        self.pipeline.get_by_name('sink').connect('new-sample', self._on_sample)
        self.bus = self.pipeline.get_bus()
        self.pipeline.set_state(Gst.State.PLAYING)

        self.create_timer(0.5, self._poll_bus)
        self.create_timer(10.0, self._report)
        self.get_logger().info(
            f'front camera: {param("width")}x{param("height")} JPEG q{param("quality")}, '
            f'max {rate:g} Hz on front_camera/image/compressed')

    def _on_sample(self, sink):
        """Runs in the GStreamer streaming thread, once per decoded frame."""
        sample = sink.emit('pull-sample')
        if sample is None:
            return Gst.FlowReturn.OK
        now = time.monotonic()
        self._last_frame = now
        # Fixed-step schedule rather than "time since the last frame": with
        # ~15 fps in and a 10 Hz cap, the latter keeps one frame in two
        # (measured 7.0 Hz); this keeps two in three.
        if now < self._next_due:
            return Gst.FlowReturn.OK
        self._next_due = max(self._next_due + self._min_period, now - self._min_period)

        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        try:
            msg = CompressedImage()
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.header.frame_id = self._frame_id
            msg.format = 'jpeg'
            msg.data = array.array('B', info.data)
        finally:
            buf.unmap(info)
        self.pub.publish(msg)
        self._published += 1
        return Gst.FlowReturn.OK

    def _poll_bus(self):
        while True:
            msg = self.bus.pop()
            if msg is None:
                break
            if msg.type == Gst.MessageType.ERROR:
                err, debug = msg.parse_error()
                self.get_logger().error(f'GStreamer: {err.message} ({debug})')
            elif msg.type == Gst.MessageType.EOS:
                self.get_logger().error('GStreamer: end of stream')

    def _report(self):
        silent = time.monotonic() - self._last_frame
        if silent > 3.0:
            self.get_logger().warn(
                f'no frame for {silent:.0f} s: is the robot on, and is the multicast '
                f'reaching {self.get_parameter("iface").value}?')
        else:
            self.get_logger().info(
                f'{self._published / 10.0:.1f} frames/s published', throttle_duration_sec=60.0)
        self._published = 0

    def destroy_node(self):
        self.pipeline.set_state(Gst.State.NULL)
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = FrontCamera()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
