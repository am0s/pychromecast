"""
PyChromecast: remote control your Chromecast
"""
from __future__ import print_function

# pylint: disable=wildcard-import
from .error import *  # noqa
# pylint: disable=wildcard-import
from .config import *  # noqa
from .device import Chromecast, IDLE_APP_ID, IGNORE_CEC, DEFAULT_PORT
from .discovery import discover_chromecasts
from .dial import (
    CAST_TYPE_CHROMECAST,
    CAST_TYPE_AUDIO,
    CAST_TYPE_GROUP,
)
from .controllers.media import STREAM_TYPE_BUFFERED  # noqa

__all__ = (
    '__version__', '__version_info__',
    'get_chromecasts', 'get_chromecasts_as_dict', 'get_chromecast',
    'Chromecast',
    'IDLE_APP_ID', 'IGNORE_CEC', 'DEFAULT_PORT',
    'CAST_TYPE_CHROMECAST', 'CAST_TYPE_AUDIO', 'CAST_TYPE_GROUP'
)
__version_info__ = ('0', '7')
__version__ = '.'.join(__version_info__)


def _get_all_chromecasts(tries=None, retry_wait=None, timeout=None,
                         browser=None, discover_timeout=None,
                         filters=None):
    """
    Returns a list of all chromecasts on the network as PyChromecast
    objects.
    """
    return discover_chromecasts(
        tries=tries, retry_wait=retry_wait, timeout=timeout,
        browser=browser, discover_timeout=discover_timeout,
        filters=filters,
    )


def get_chromecasts(tries=None, retry_wait=None, timeout=None,
                    browser=None, discover_timeout=None, **filters):
    """
    Searches the network and returns a list of Chromecast objects.
    Filter is a list of options to filter the chromecasts by.

    ex: get_chromecasts(friendly_name="Living Room")

    May return an empty list if no chromecasts were found matching
    the filter criteria

    Filters include dial.DeviceStatus items:
        friendly_name, model_name, manufacturer, api_version
    Or AppStatus items:
        app_id, description, state, service_url, service_protocols (list)
    Or ip address:
        ip

    Tries is specified if you want to limit the number of times the
    underlying socket associated with your Chromecast objects will
    retry connecting if connection is lost or it fails to connect
    in the first place. The number of seconds spent between each retry
    can be defined by passing the retry_wait parameter, the default is
    to wait 5 seconds.

    The maximum time it will use to wait for devices is controlled by
    discover_timeout, the default is 5 seconds but can be overriden.
    Note: If the filter criteria is met the function may return earlier
    then the max discover timeout.

    :type tries: int
    :type retry_wait: float
    :type timeout: float
    :type discover_timeout: float
    :type browser: .discovery.CastBrowser
    """
    # logger = logging.getLogger(__name__)

    cc_list = _get_all_chromecasts(
            tries, retry_wait, timeout, browser, discover_timeout, filters)

    return cc_list


def get_chromecasts_as_dict(tries=None, retry_wait=None, timeout=None,
                            browser=None, discover_timeout=None, **filters):
    """
    Returns a dictionary of chromecasts with the friendly name as
    the key.  The value is the pychromecast object itself.

    Tries is specified if you want to limit the number of times the
    underlying socket associated with your Chromecast objects will
    retry connecting if connection is lost or it fails to connect
    in the first place. The number of seconds spent between each retry
    can be defined by passing the retry_wait parameter, the default is
    to wait 5 seconds.

    :type tries: int
    :type retry_wait: float
    :type timeout: float
    :type discover_timeout: float
    :type browser: .discovery.CastBrowser
    """
    return {cc.device.friendly_name: cc
            for cc in get_chromecasts(
                tries=tries, retry_wait=retry_wait, timeout=timeout,
                browser=browser, discover_timeout=discover_timeout,
                **filters)}


def get_chromecast(strict=False, tries=None, retry_wait=None, timeout=None,
                   browser=None, discover_timeout=None, **filters):
    """
    Same as get_chromecasts but only if filter matches exactly one
    ChromeCast.

    Returns a Chromecast matching exactly the fitler specified.

    If strict, return one and only one chromecast

    Tries is specified if you want to limit the number of times the
    underlying socket associated with your Chromecast objects will
    retry connecting if connection is lost or it fails to connect
    in the first place. The number of seconds spent between each retry
    can be defined by passing the retry_wait parameter, the default is
    to wait 5 seconds.

    :type tries: int
    :type retry_wait: float
    :type timeout: float
    :type discover_timeout: float
    :type browser: .discovery.CastBrowser
    """

    # If we have filters or are operating in strict mode we have to scan
    # for all Chromecasts to ensure there is only 1 matching chromecast.
    # If no filters given and not strict just use the first discovered one.
    if filters or strict:
        results = get_chromecasts(
            tries=tries, retry_wait=retry_wait, timeout=timeout,
            browser=browser, discover_timeout=discover_timeout,
            **filters)
    else:
        results = _get_all_chromecasts(tries, retry_wait)

    if len(results) > 1:
        if strict:
            raise MultipleChromecastsFoundError(
                'More than one Chromecast was found specifying '
                'the filter criteria: {}'.format(filters))
        else:
            return results[0]

    elif not results:
        if strict:
            raise NoChromecastFoundError(
                'No Chromecasts matching filter critera were found:'
                ' {}'.format(filters))
        else:
            return None

    else:
        return results[0]
