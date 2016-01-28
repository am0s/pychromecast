import sys
import logging
import fnmatch
import threading

# pylint: disable=wildcard-import
#from .error import *  # noqa
from .error import ChromecastConnectionError, NotConnected

from . import socket_client
from .dial import get_device_status, reboot, DeviceStatus


IDLE_APP_ID = 'E8C28D3C'
IGNORE_CEC = []
# For Python 2.x we need to decode __repr__ Unicode return values to str
NON_UNICODE_REPR = sys.version_info < (3, )
DEFAULT_PORT = 8009


# pylint: disable=too-many-instance-attributes
class Chromecast(object):
    """
    Class to interface with a ChromeCast.

    :param port: The port to use when connecting to the device, set to None to
                 use the default of 8009. Special devices such as Cast Groups
                 may return a different port number so we need to use that.
    :param device: DeviceStatus with initial information for the device.
    :type device: pychromecast.dial.DeviceStatus
    :param tries: Number of retries to perform if the connection fails.
                  None for inifinite retries.
    :param timeout: A floating point number specifying the socket timeout in
                    seconds. None means to use the default which is 30 seconds.
    :param retry_wait: A floating point number specifying how many seconds to
                       wait between each retry. None means to use the default
                       which is 5 seconds.
    :param connect: If True then it will connect to the device immediately,
            if False it just sets up the instance.
    :type connect: bool
    """

    def __init__(self, host, port=None, device=None, **kwargs):
        self.tries = kwargs.pop('tries', None)
        self.timeout = kwargs.pop('timeout', None)
        self.retry_wait = kwargs.pop('retry_wait', None)
        connect = kwargs.pop('connect', True)

        self.logger = logging.getLogger('pychromecast')

        # Resolve host to IP address
        self.host = host
        self.port = port or 8009

        self.logger.info("Querying device status")
        self.device = device
        if device:
            dev_status = get_device_status(self.host)
            if dev_status:
                # Values from `device` have priority over `dev_status`
                # as they come from the dial information.
                # `dev_status` may add extra information such as `manufacturer`
                # which dial does not supply
                self.device = DeviceStatus(
                    friendly_name=(device.friendly_name or
                                   dev_status.friendly_name),
                    model_name=(device.model_name or
                                dev_status.model_name),
                    manufacturer=(device.manufacturer or
                                  dev_status.manufacturer),
                    api_version=(device.api_version or
                                 dev_status.api_version),
                    uuid=(device.uuid or
                          dev_status.uuid),
                    cast_type=(device.cast_type or
                               dev_status.cast_type),
                )
            else:
                self.device = device
        else:
            self.device = get_device_status(self.host)

        if not self.device:
            raise ChromecastConnectionError(
                "Could not connect to {}:{}".format(self.host, self.port))

        self.status = None
        self.status_event = threading.Event()
        self.is_started = False
        self._delayed_handlers = []
        self._delayed_status_listeners = []
        self._delayed_launch_error_listeners = []
        self._delayed_connection_listeners = []

        def set_volume_void(volume):
            return volume

        def set_volume_muted_void(muted):
            pass

        def play_media_void(*args, **kwargs):
            pass

        self.socket_client = None
        self.set_volume = set_volume_void
        self.set_volume_muted = set_volume_muted_void
        self.play_media = play_media_void
        self.register_handler = self._delayed_register_handler
        self.register_status_listener = self._delayed_register_status_listener
        self.register_launch_error_listener = \
            self._delayed_register_launch_error_listener
        self.register_connection_listener = \
            self._delayed_register_connection_listener

        if connect:
            self.connect()

    def _delayed_register_handler(self, handler):
        self._delayed_handlers.append(handler)

    def _delayed_register_status_listener(self, listener):
        self._delayed_status_listeners.append(listener)

    def _delayed_register_launch_error_listener(self, listener):
        self._delayed_launch_error_listeners.append(listener)

    def _delayed_register_connection_listener(self, listener):
        self._delayed_connection_listeners.append(listener)

    def connect(self):
        if self.is_started:
            return

        self.is_started = True

        self.socket_client = socket_client.SocketClient(
            self.host, port=self.port, cast_type=self.device.cast_type,
            tries=self.tries, timeout=self.timeout, retry_wait=self.retry_wait)

        receiver_controller = self.socket_client.receiver_controller
        receiver_controller.register_status_listener(self)

        # Forward these methods
        self.set_volume = receiver_controller.set_volume
        self.set_volume_muted = receiver_controller.set_volume_muted
        self.play_media = self.socket_client.media_controller.play_media
        self.register_handler = self.socket_client.register_handler
        self.register_status_listener = \
            receiver_controller.register_status_listener
        self.register_launch_error_listener = \
            receiver_controller.register_launch_error_listener
        self.register_connection_listener = \
            self.socket_client.register_connection_listener

        if self._delayed_handlers:
            delayed_handlers = self._delayed_handlers
            self._delayed_handlers = []
            for handler in delayed_handlers:
                self.register_handler(handler)

        if self._delayed_status_listeners:
            delayed_listeners = self._delayed_status_listeners
            self._delayed_status_listeners = []
            for listener in delayed_listeners:
                self.register_status_listener(listener)

        if self._delayed_launch_error_listeners:
            delayed_listeners = self._delayed_launch_error_listeners
            self._delayed_launch_error_listeners = []
            for listener in delayed_listeners:
                self.register_status_listener(listener)

        if self._delayed_connection_listeners:
            delayed_listeners = self._delayed_connection_listeners
            self._delayed_connection_listeners = []
            for listener in delayed_listeners:
                self.register_connection_listener(listener)

        self.socket_client.start()

    @property
    def ignore_cec(self):
        """ Returns whether the CEC data should be ignored. """
        return self.device is not None and \
            any([fnmatch.fnmatchcase(self.device.friendly_name, pattern)
                 for pattern in IGNORE_CEC])

    @property
    def is_connected(self):
        """ Returns if it is currently connected to a Chromecast. """
        return (self.is_started and self.socket_client and
                self.socket_client.is_connected)

    @property
    def is_idle(self):
        """ Returns if there is currently an app running. """
        return (self.status is None or
                self.app_id in (None, IDLE_APP_ID) or
                (not self.status.is_active_input and not self.ignore_cec))

    @property
    def uuid(self):
        """ Returns the unique UUID of the Chromecast device. """
        return self.device.uuid

    @property
    def name(self):
        """
        Returns the friendly name set for the Chromecast device.
        This is the name that the end-user chooses for the cast device.
        """
        return self.device.friendly_name

    @property
    def model_name(self):
        """ Returns the model name of the Chromecast device. """
        return self.device.model_name

    @property
    def cast_type(self):
        """
        Returns the type of the Chromecast device.
        This is one of CAST_TYPE_CHROMECAST for regular Chromecast device,
        CAST_TYPE_AUDIO for Chromecast devices that only support audio
        and CAST_TYPE_GROUP for virtual a Chromecast device that groups
        together two or more cast (Audio for now) devices.

        :rtype: str
        """
        return self.device.cast_type

    @property
    def app_id(self):
        """ Returns the current app_id. """
        return self.status.app_id if self.status else None

    @property
    def app_display_name(self):
        """ Returns the name of the current running app. """
        return self.status.display_name if self.status else None

    @property
    def media_controller(self):
        """ Returns the media controller. """
        if self.socket_client:
            return self.socket_client.media_controller
        else:
            return None

    def new_cast_status(self, status):
        """ Called when a new status received from the Chromecast. """
        self.status = status
        if status:
            self.status_event.set()

    def start_app(self, app_id):
        """ Start an app on the Chromecast. """
        if not self.is_started:
            raise NotConnected("Cannot start an app without being connected")

        self.logger.info("Starting app %s", app_id)

        self.socket_client.receiver_controller.launch_app(app_id)

    def quit_app(self):
        """ Tells the Chromecast to quit current app_id. """
        if not self.is_started:
            raise NotConnected("Cannot quit an app without being connected")

        self.logger.info("Quiting current app")

        self.socket_client.receiver_controller.stop_app()

    def reboot(self):
        """ Reboots the Chromecast. """
        reboot(self.host)

    def volume_up(self):
        """ Increment volume by 0.1 unless it is already maxed.
        Returns the new volume.

        """
        volume = round(self.status.volume_level, 1)
        return self.set_volume(volume + 0.1)

    def volume_down(self):
        """ Decrement the volume by 0.1 unless it is already 0.
        Returns the new volume.
        """
        volume = round(self.status.volume_level, 1)
        return self.set_volume(volume - 0.1)

    def wait(self, timeout=None):
        """
        Waits until the cast device is ready for communication. The device
        is ready as soon a status message has been received.

        If the status has already been received then the method returns
        immediately.

        :param timeout: a floating point number specifying a timeout for the
                        operation in seconds (or fractions thereof). Or None
                        to block forever.
        """
        self.status_event.wait(timeout=timeout)

    def disconnect(self, timeout=None, blocking=True):
        """
        Disconnects the chromecast and waits for it to terminate.

        :param timeout: a floating point number specifying a timeout for the
                        operation in seconds (or fractions thereof). Or None
                        to block forever.
        :param blocking: If True it will block until the disconnection is
                         complete, otherwise it will return immediately.
        """
        if self.is_started:
            self.socket_client.disconnect()
            if blocking:
                self.join(timeout=timeout)
            self.is_started = False

    def join(self, timeout=None):
        """
        Blocks the thread of the caller until the chromecast connection is
        stopped.

        :param timeout: a floating point number specifying a timeout for the
                        operation in seconds (or fractions thereof). Or None
                        to block forever.
        """
        if self.is_started:
            self.socket_client.join(timeout=timeout)

    def __del__(self):
        if self.is_started:
            self.socket_client.stop.set()

    def __repr__(self):
        txt = u"Chromecast({!r}, port={!r}, device={!r})".format(
            self.host, self.port, self.device)
        # Python 2.x does not work well with unicode returned from repr
        if NON_UNICODE_REPR:
            return txt.encode('utf-8')
        return txt

    def __unicode__(self):
        return u"Chromecast({}, {}, {}, {}, {}, api={}.{})".format(
            self.host, self.port, self.device.friendly_name,
            self.device.model_name, self.device.manufacturer,
            self.device.api_version[0], self.device.api_version[1])
