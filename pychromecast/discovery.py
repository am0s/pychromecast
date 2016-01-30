"""Discovers Chromecasts on the network using mDNS/zeroconf."""
import logging
import time
from abc import abstractmethod
from collections import namedtuple
from uuid import UUID
# Python 3 has renamed Queue to queue, support both options
try:
    import queue
except ImportError:
    import Queue as queue

import six
from zeroconf import ServiceBrowser, Zeroconf
from .dial import (
    CAST_TYPES,
    CAST_TYPE_CHROMECAST,
    DeviceStatus,
    NetworkAddress,
)
from .device import Chromecast, DEFAULT_PORT


# The minimum block time allowed for the queue
QUEUE_MIN_TIMEOUT = 0.01
# The maximum number of seconds to wait before returning chromecast devices
# It is the default unless specified by the caller
DISCOVER_TIMEOUT = 5


DiscoveryStatus = namedtuple('DiscoveryStatus', [
    'device',
    'status',
])


def discover_chromecasts(max_devices=None, discover_timeout=None,
                         browser=None, filters=None, connect=True, **kwargs):
    """
    Discover chromecasts on the network for a specified amount of time and
    return a list of discovered devices.
    If filters are used the function may return earlier if the filter
    criteria is met, e.g. if looking for one specific device.

    :type max_devices: int
    :param discover_timeout: The maximum number of seconds to wait for devices.
            None means to use the defaults and False disables the timeout.
    :type discover_timeout: float
    :param browser: The browser instance to use for discovery, or None to
            create a new one for this run only.
    :type browser: CastBrowser
    :type filters: dict
    :param connect: If True then it will connect to the Chromecast devices
            before returning.
    :type connect: bool
    :rtype: list[Chromecast]
    """
    status_filter = FilteredDeviceQueue(
        max_devices=max_devices, filters=filters)
    cleanup = False
    if not browser:
        browser = CastBrowser(**kwargs)
        cleanup = False

    if discover_timeout is False:
        discover_timeout = None
    elif discover_timeout is None:
        discover_timeout = DISCOVER_TIMEOUT

    try:
        return browser.discover(
            status_filter, timeout=discover_timeout, connect=connect)
    finally:
        if cleanup:
            browser.stop()


# pylint: disable=too-few-public-methods
class ListenerBase(object):
    """
    Base class for Chromecast listeners. The listener can be registered
    in a CastBrowser to get notified about discovered Chromecast devices.
    """

    @abstractmethod
    def new_device_status(self, status):
        """
        Called whenever we receive a new status about a device.

        :type status: DiscoveryStatus
        """
        pass


class EnqueueListener(ListenerBase):
    """
    Listener which sends all discovered devices to a chosen queue.

    :type device_queue: FilteredDeviceQueueBase
    :param connect: If True then it will connect to the device before
        passing it to the queue handler.
    :type connect: bool
    """

    def __init__(self, device_queue, connect=False):
        self.queue = device_queue
        self.connect = connect

    def new_device_status(self, status):
        """
        :type status: DiscoveryStatus
        """
        # Pass on the device to the queue
        if self.connect:
            status.device.connect()
            status.device.wait()
        self.queue.enqueue_device(status.device)


class FilteredDeviceQueueBase(object):
    """
    Base class for a queue of Chromecast devices with filtering support.
    The queue accepts discovered devices from another thread by using
    enqueue_device(). The main thread should call process() which will
    block until the filter criteria are met or it times out.
    """

    # pylint: disable=unused-argument
    def __init__(self, **kwargs):
        self.queue = queue.Queue()
        self._devices = []

    @property
    def devices(self):
        """
        The devices currently in the queue.
        """
        return self._devices

    def enqueue_device(self, device):
        """
        Enqueues a new device to the incoming queue and wakes the thread
        blocking on the queue.
        :param device: Chromecast
        """
        # Place the item in the queue but do not wait.
        # This will wake the other thread blocking on the queue and allow
        # it to filter the device.
        self.queue.put_nowait(device)

    def process(self, timeout=None):
        """
        Starts processing the incoming queue for new devices and optionally
        filter them. This will block the current thread until a timeout
        occurs or the filtered criteria are met. If there are no filters and
        no timeout it will process indefinitely.

        :param timeout: The maximum time to process, or None to wait until
               the wanted devices are found.
        :type timeout: float
        :return: A list of devices which were received.
        :rtype: list[Chromecast]
        """
        start = time.time()
        while True:
            try:
                block_timeout = None
                # If a timeout is specified we set the block timeout to the
                # remaining time, but not less than QUEUE_MIN_TIMEOUT.
                if timeout:
                    block_timeout = max(timeout - (time.time() - start),
                                        QUEUE_MIN_TIMEOUT)
                cast = self.queue.get(True, block_timeout)
                if cast:
                    if self.filter(cast):
                        self._devices.append(cast)
                if self.is_full():
                    break
            except queue.Empty:
                pass
            if timeout is not None and time.time() - start >= timeout:
                break
        return self._devices

    # pylint: disable=unused-argument,no-self-use
    def filter(self, cast_device):
        """
        Filters the cast device by checking the filter critera and returns
        True if the device passes, False otherwise.

        :type cast_device: Chromecast
        :rtype: bool
        """
        return True

    # pylint: disable=no-self-use
    def is_full(self):
        """
        Checks if the queue is full, returns True if full, False otherwise.

        The default returns False as there is no limit on the queue.
        :rtype: bool
        """
        return False

    # pylint: disable=no-self-use
    def is_connection_required(self):
        """
        Returns True if the queue requires a connection with the Chromecast
        before receiving the device.

        :rtype: bool
        """
        return False


# pylint: disable=too-many-instance-attributes
class FilteredDeviceQueue(FilteredDeviceQueueBase):
    """
    A queue of Chromecast devices with an optional filter and limit on
    how many devices to find.
    The filters refer to the attributes in the DeviceStatus or Status
    classes.
    The following filter attributes will match a unique device and will
    stop the process once found:
    - ip - The ip of the host, can be an ip string, tuple(ip, port) or
           a NetworkAddress instance. If no port is specified it assumes
           the default port.
    - friendly_name - The unique human-friendly name of the device, e.g.
                      "Living room" or "TV".
    - uuid - A unique UUID of the device, this uniquely identifies the
             device without knowing the friendly name.

    The following filter attributes may match more than one device and
    will not stop the process until the timeout occurs, or max devices
    is met.
    - model_name - The model name for the device.
    - cast_type - The type of Chromecast, 'chromecast', 'audio' or 'group'.
    - app_id - The unique ID of the app running.
    - display_name - The name reported by the running app.
    - session_id - The unique session ID for a given chromecast.

    See socketclient.CastStatus and dial.DeviceStatus for more fields to
    use.

    :param max_devices: Limit the queue to this many devices.
    :type max_devices: int
    :param filters: The filters to use when receiving devices.
    :type filters: dict
    """

    def __init__(self, max_devices=None, filters=None, **kwargs):
        self.max_devices = max_devices
        self.extra_filters = {}
        self.unique_match = False
        self.no_filters = True

        self.friendly_name = None
        self.uuid = None
        self.host = None
        self.model_name = None

        if filters:
            # If these filters are used it will result in one unique Chromecast
            # which means we stop as soon the criteria is met.
            self.friendly_name = filters.pop('friendly_name', None)
            uuid = filters.pop('uuid', None)
            if uuid:
                if not isinstance(uuid, UUID):
                    uuid = UUID(uuid)
                self.uuid = uuid
            address = filters.pop('ip', None)
            if address:
                if isinstance(address, tuple):
                    host = NetworkAddress(address[0], address[1])
                elif isinstance(address, NetworkAddress):
                    host = address
                else:
                    host = NetworkAddress(address, DEFAULT_PORT)
                self.host = host

            # These filters can match more than one device
            self.model_name = filters.pop('model_name', None)

            self.extra_filters = filters

        self.unique_match = bool(
            self.friendly_name is not None or
            self.uuid is not None or
            self.host is not None
        )

        self.no_filters = bool(
            self.friendly_name is None and
            self.uuid is None and
            self.host is None and
            self.model_name is None and
            not self.extra_filters
        )

        super(FilteredDeviceQueue, self).__init__(**kwargs)

    # pylint: disable=too-many-return-statements
    def filter(self, cast_device):
        """
        :type cast_device: Chromecast
        :rtype: bool
        """
        if self.no_filters:
            return True

        status = cast_device.device
        if self.friendly_name is not None and \
           self.friendly_name == status.friendly_name:
            return True
        if self.uuid is not None and self.uuid == status.uuid:
            return True
        if self.host is not None and \
           self.host.address == cast_device.host and \
           self.host.port == cast_device.port:
            return True

        if self.model_name is not None and \
           self.model_name == status.model_name:
            return True

        for key, val in self.extra_filters.items():
            for obj in (cast_device.device, cast_device.status):
                if hasattr(obj, key) and val == getattr(obj, key):
                    return True

        return False

    def is_full(self):
        if self.unique_match and self.devices:
            return True
        if self.max_devices is not None and \
           len(self.devices) >= self.max_devices:
            return True
        return False

    def is_connection_required(self):
        return bool(self.extra_filters)


class ZeroconfBrowser(ServiceBrowser):
    """
    Wrapper around the Zeroconf ServiceBrowser, used to catch certain
    exceptions which occurs during shutdown, these errors should be ignored.
    """
    def __init__(self, *args, **kwargs):
        self.logger = logging.getLogger(__name__)
        super(ZeroconfBrowser, self).__init__(*args, **kwargs)

    def run(self):
        try:
            super(ZeroconfBrowser, self).run()
        except OSError:
            # Ignore the error if we are meant to stop, this can happen
            # if we stop the browser while it writes to a socket.
            if not self.done:
                self.logger.exception(
                    u'Exception in service browser thread'
                )


class CastBrowser(object):
    """
    Is responsible for discovering Chromecast devices on the network and
    manage them.
    Listeners may register themselves to get reported and added or removed
    devices.

    To discover devices asynchronously the client should create the browser,
    register any listener(s) and then call start() to initiate
    a new thread which discovers devices.

    For synchronous usage create the browser and call the discover() method
    with a filter instance. This will then block the main thread until the
    filter criteria has been fulfilled or it times out.

    When the browser is no longer to be used the stop() method may be called.

    :param tries: Default 'tries' value for new Chromecast devices.
    :type tries: int
    :param timeout: Default 'timeout' value for new Chromecast devices.
    :type timeout: float
    :param retry_wait: Default 'retry_wait' value for new Chromecast devices.
    :type retry_wait: float
    """

    browser = None

    def __init__(self, **kwargs):
        self.tries = kwargs.pop('tries', None)
        self.timeout = kwargs.pop('timeout', None)
        self.retry_wait = kwargs.pop('retry_wait', None)
        self.zconf = None
        self.browser = None
        self.start_time = None
        self.services = {}
        self.cast_map = {}
        self.listeners = []

    def start(self):
        """
        Start the discovery process by looking for zerconf devices.
        """
        assert self.browser is None
        self.zconf = Zeroconf()
        self.browser = ZeroconfBrowser(
            self.zconf, "_googlecast._tcp.local.", self)
        self.start_time = time.time()

    def stop(self):
        """
        Stop the discovery process and disconnect any active chromecast
        devices.

        """
        if self.browser:
            self.browser.cancel()
        if self.zconf:
            self.zconf.close()
        if self.browser:
            self.browser.join()

        for cast in self.devices:
            cast.disconnect()

    def discover(self, device_queue=None, timeout=DISCOVER_TIMEOUT,
                 connect=True):
        """
        :type device_queue: FilteredDeviceQueueBase
        :type timeout: float
        :param connect: If True then the cast devices will be connected to
                before returning them (if not connected).
        :type connect: bool
        """
        if not self.browser:
            self.start()

        if not device_queue and timeout is None:
            return self.devices

        if not device_queue:
            device_queue = FilteredDeviceQueue()

        # Create a listener which will pass new devices to the filter
        listener = EnqueueListener(
            device_queue, connect=device_queue.is_connection_required())
        self.register_listener(listener)

        # Add all devices which have already been discovered
        for device in self.devices:
            device_queue.enqueue_device(device)

        devices = device_queue.process(timeout)

        self.unregister_listener(listener)

        if connect:
            # Start the devices if not already started
            for device in devices:
                device.connect()

        return devices

    @property
    def count(self):
        """
        Number of discovered cast services.

        :rtype: int
        """
        return len(self.services)

    @property
    def devices(self):
        """
        List of Chromecast devices which are discovered.

        :rtype: list[Chromecast]
        """
        return list(self.services.values())

    # pylint: disable=unused-argument
    def remove_service(self, zconf, typ, name):
        """
        Remove a service from the collection.
        """
        self.remove_cast_device(name)

    def add_service(self, zconf, typ, name):
        """
        Called whenever a new service is discovered. A new Chromecast device
        is created from this information, added to the collection and then
        reported to any listeners.
        """
        service = None
        tries = 0
        while service is None and tries < 4:
            try:
                service = zconf.get_service_info(typ, name)
            except IOError:
                # If zeroconf fails to receive the necessary data we abort
                # adding the service
                break
            tries += 1

        if not service:
            return

        def get_value(key):
            """Retrieve value and decode for Python 2/3."""
            value = service.properties.get(key.encode('utf-8'))

            if value is None or isinstance(value, six.text_type):
                return value
            return value.decode('utf-8')

        ips = zconf.cache.entries_with_name(service.server.lower())
        host = repr(ips[0]) if ips else service.server

        model_name = get_value('md')
        uuid = get_value('id')
        friendly_name = get_value('fn')

        if uuid:
            uuid = UUID(uuid)

        cast_device = self.create_cast_device(
            name, host, service.port, uuid, model_name, friendly_name)
        self.add_cast_device(name, cast_device)

    # pylint: disable=too-many-arguments
    def create_cast_device(self, name, host, port, uuid, model_name,
                           friendly_name):
        """
        Creates a new Chromecast device from the parameters and returns it.
        """
        cast_type = CAST_TYPES.get(model_name.lower(),
                                   CAST_TYPE_CHROMECAST)
        device = DeviceStatus(
            friendly_name=friendly_name, model_name=model_name,
            manufacturer=None, api_version=None,
            uuid=uuid, cast_type=cast_type,
        )
        return Chromecast(
            host=host, port=port,
            device=device,
            tries=self.tries, timeout=self.timeout, retry_wait=self.retry_wait,
            connect=False,
        )

    def release_cast_device(self, cast_device):
        """
        Releases the Chromecast device from this browser. This means that
        the device is no longer managed and it is up the caller to disconnect.

        :param cast_device: Chromecast
        """
        for name, device in self.services.items():
            if device == cast_device:
                del self.services[name]
                break

    def add_cast_device(self, name, cast_device):
        """
        Adds the cast device to the browser and register it under a unique
        name. The device will then be managed by the browser for its lifetime.
        Use the method release_cast_device to stop management of the device.

        The new device will also be reported to any listeners.

        :param name: mDNS name for the device.
        :param cast_device: The Chromecast device.
        :type cast_device: Chromecast
        """
        self.services[name] = cast_device
        self.report_device_status(DiscoveryStatus(cast_device, 'added'))

    def remove_cast_device(self, name):
        """
        Call whenever a cast devices is removed or no longer available.
        The device will then be released from this browser.

        The device will then be reported to any listeners to let them know
        that it has been removed.

        :param name: mDNS name for the device.
        """
        if name in self.services:
            cast_device = self.services.pop(name)
            self.report_device_status(DiscoveryStatus(cast_device, 'removed'))

    def report_device_status(self, status):
        """
        Reports the discovery status to all listeners.

        :param status: The status value
        :type status: DiscoveryStatus
        """
        for listener in self.listeners:
            try:
                listener.new_device_status(status)
            except Exception:  # pylint: disable=broad-except
                pass

    def register_listener(self, listener):
        """
        Registers a new discovery listener. The listener will be notified
        whenever there is a new device available, or a device is removed.

        :param listener: The listener instance
        :type listener: ListenerBase
        """
        self.listeners.append(listener)

    def unregister_listener(self, listener):
        """
        Unregisters an existing discovery listener.

        :param listener: The listener instance
        :type listener: ListenerBase
        """
        self.listeners.remove(listener)
