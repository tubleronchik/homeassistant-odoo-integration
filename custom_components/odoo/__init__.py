import asyncio
import functools
import xmlrpc.client
import typing as tp
import logging
from datetime import datetime
from .const import DOMAIN, CREATE_ORDERS_SERVICE, DB, HOST, PORT, USERNAME, PASSWORD
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.typing import ConfigType
from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


def to_thread(func: tp.Callable) -> tp.Coroutine:
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        return await asyncio.to_thread(func, *args, **kwargs)

    return wrapper


@to_thread
def connect_to_db(entry: ConfigEntry) -> tuple() | None:
    url = f"{entry.data[HOST]}:{entry.data[PORT]}"
    try:
        common = xmlrpc.client.ServerProxy("{}/xmlrpc/2/common".format(url), allow_none=1)
        uid = common.authenticate(entry.data[DB], entry.data[USERNAME], entry.data[PASSWORD], {})
        if uid == 0:
            _LOGGER.error("Could not connect to db. Credentials are wrong!")
            raise Exception("Credentials are wrong for remote system access")
        else:
            _LOGGER.debug(f"Connection to the db stablished successfully. User id is: {uid}")
            connection = xmlrpc.client.ServerProxy("{}/xmlrpc/2/object".format(url))
            return connection, uid
    except Exception as e:
        _LOGGER.error(f"Could not connect to the db with the error: {e}")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Robonomics Integration from a config entry.
    It calls every time integration uploading and after config flow during initial
    setup.
    :param hass: HomeAssistant instance
    :param entry: Data from config
    :return: True after succesfull setting up
    """
    connection, uid = await connect_to_db(entry)

    async def handle_create_order(call: ServiceCall) -> None:
        """Callback for create_order service"""
        name = call.data["name"]
        order_id = await _create_order(name)

    @to_thread
    def _create_order(name: str) -> int:
        location_id = 2
        todo = "some job"
        worker_id = 6
        priority = 3
        scheduled_date_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        scheduled_duration = 1.0
        order_id = connection.execute_kw(
            entry.data[DB],
            uid,
            entry.data[PASSWORD],
            "fsm.order",
            "create",
            [
                {
                    "location_id": location_id,
                    "todo": todo,
                    "person_id": worker_id,
                    "priority": priority,
                    "name": name,
                    "scheduled_date_start": scheduled_date_start,
                    "scheduled_duration": scheduled_duration
                }
            ],
        )
        return order_id

    hass.services.async_register(DOMAIN, CREATE_ORDERS_SERVICE, handle_create_order)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    return True
