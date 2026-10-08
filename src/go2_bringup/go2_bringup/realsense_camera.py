#!/usr/bin/env python3
"""The RealSense D435i on the Jetson's USB-C port, as compressed images.

Not the robot's own camera (that is front_camera.py): this is the Intel
RealSense plugged into the Jetson, which the USB-C port only powers once
/etc/rc.local has run (jetson/devmem_write.py).

Publishes, for looking at from the PC (docs/remote-ros.md):

    realsense/color/image/compressed          JPEG, 640x480, 10 Hz
    realsense/depth_colored/image/compressed  depth as a JPEG colour map, 5 Hz:
                                              warm = near, cold = far,
                                              black = no reading

The colour map is for eyes only. Anything that needs metric depth should read
the camera on the Jetson itself rather than through this node.

The pipeline restarts by itself when the camera goes away: the Wi-Fi watchdog
resets the only USB controller, which the RealSense shares with the dongle,
and a cable can be unplugged and plugged back in.

Uses pyrealsense2 and the system OpenCV, both already on the Jetson.

    ros2 run go2_bringup realsense_camera --ros-args -p max_rate:=10.0 -p depth:=true
"""

import array
import threading
import time

import cv2
import numpy as np
import pyrealsense2 as rs
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage


class Schedule:
    """Fixed-step rate cap: two frames in three for 15 fps in and 10 Hz out."""

    def __init__(self, rate):
        self.period = 1.0 / rate if rate > 0 else 0.0
        self.next_due = 0.0

    def due(self, now):
        if now < self.next_due:
            return False
        self.next_due = max(self.next_due + self.period, now - self.period)
        return True


class RealSenseCamera(Node):
    def __init__(self):
        super().__init__('go2_realsense_camera')
        self.declare_parameter('width', 640)
        self.declare_parameter('height', 480)
        self.declare_parameter('fps', 15)
        self.declare_parameter('max_rate', 10.0)
        self.declare_parameter('quality', 80)
        self.declare_parameter('depth', True)
        self.declare_parameter('depth_max_rate', 5.0)
        self.declare_parameter('depth_min_m', 0.2)
        self.declare_parameter('depth_max_m', 4.0)
        self.declare_parameter('frame_id', 'realsense_color_optical_frame')

        def param(name):
            return self.get_parameter(name).value

        self._width, self._height = int(param('width')), int(param('height'))
        self._fps = int(param('fps'))
        self._quality = int(param('quality'))
        self._depth = bool(param('depth'))
        self._depth_min, self._depth_max = float(param('depth_min_m')), float(param('depth_max_m'))
        self._frame_id = param('frame_id')
        self._color_rate = Schedule(float(param('max_rate')))
        self._depth_rate = Schedule(float(param('depth_max_rate')))
        self._counts = {'color': 0, 'depth': 0}

        self.color_pub = self.create_publisher(
            CompressedImage, 'realsense/color/image/compressed', qos_profile_sensor_data)
        self.depth_pub = self.create_publisher(
            CompressedImage, 'realsense/depth_colored/image/compressed', qos_profile_sensor_data)

        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.create_timer(10.0, self._report)

    def _start(self):
        pipeline = rs.pipeline()
        config = rs.config()
        config.enable_stream(rs.stream.color, self._width, self._height, rs.format.bgr8, self._fps)
        if self._depth:
            config.enable_stream(rs.stream.depth, self._width, self._height, rs.format.z16, self._fps)
        profile = pipeline.start(config)
        scale = profile.get_device().first_depth_sensor().get_depth_scale() if self._depth else 0.0
        name = profile.get_device().get_info(rs.camera_info.name)
        self.get_logger().info(
            f'{name}: colour {self._width}x{self._height}@{self._fps}'
            + (', depth on' if self._depth else ''))
        return pipeline, scale

    def _loop(self):
        while self._running and rclpy.ok():
            try:
                pipeline, scale = self._start()
            except RuntimeError as exc:
                self.get_logger().warn(f'RealSense not available ({exc}), retrying in 3 s',
                                       throttle_duration_sec=30.0)
                time.sleep(3.0)
                continue
            try:
                while self._running and rclpy.ok():
                    frames = pipeline.wait_for_frames(3000)
                    now = time.monotonic()
                    color = frames.get_color_frame()
                    if color and self._color_rate.due(now):
                        self._publish(self.color_pub, np.asanyarray(color.get_data()))
                        self._counts['color'] += 1
                    depth = frames.get_depth_frame() if self._depth else None
                    if depth and self._depth_rate.due(now):
                        self._publish(self.depth_pub, self._colorize(depth, scale))
                        self._counts['depth'] += 1
            except RuntimeError as exc:
                self.get_logger().warn(f'RealSense stream lost ({exc}), restarting')
            finally:
                try:
                    pipeline.stop()
                except RuntimeError:
                    pass
            time.sleep(1.0)

    def _colorize(self, depth_frame, scale):
        metres = np.asanyarray(depth_frame.get_data()).astype(np.float32) * scale
        span = self._depth_max - self._depth_min
        # near = 255 (warm in JET), far = 0 (cold)
        level = np.clip((self._depth_max - metres) / span, 0.0, 1.0) * 255.0
        image = cv2.applyColorMap(level.astype(np.uint8), cv2.COLORMAP_JET)
        image[metres <= 0.0] = 0  # no reading
        return image

    def _publish(self, publisher, image):
        ok, jpeg = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, self._quality])
        if not ok:
            return
        msg = CompressedImage()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self._frame_id
        msg.format = 'jpeg'
        msg.data = array.array('B', jpeg.tobytes())
        publisher.publish(msg)

    def _report(self):
        self.get_logger().info(
            f'{self._counts["color"] / 10.0:.1f} colour + {self._counts["depth"] / 10.0:.1f} '
            f'depth frames/s published', throttle_duration_sec=60.0)
        self._counts = {'color': 0, 'depth': 0}

    def destroy_node(self):
        self._running = False
        self._thread.join(timeout=5.0)
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = RealSenseCamera()
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
