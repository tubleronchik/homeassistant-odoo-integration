import asyncio
import functools
import xmlrpc.client
import typing as tp
import logging
from datetime import datetime
from .const import DOMAIN, CREATE_ORDERS_SERVICE, DB, HOST, PORT, USERNAME, PASSWORD
from .pubsub import subscribe_response_topic, parse_income_message
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
def connect_to_db(entry: ConfigEntry) -> tp.Optional[tuple]:
    """Connect to the database in Odoo."""

    host = entry.data[HOST]
    if host[-1] == "/":
        host = host[:-1]
    url = f"{host}:{entry.data[PORT]}"
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
    precompleted_stage_id = 18

    async def handle_create_order(call: ServiceCall) -> None:
        """Callback for create_order service"""
        
        name = call.data["name"]
        order_id = await _create_order(name)
        topic = f"odoo_change_order_stage_{order_id}"
        resp_sub = asyncio.ensure_future(
            subscribe_response_topic(topic, _subscribe_callback)
        )

        def _subscribe_callback(obj, update_nr, subscription_id) -> bool:
            """PubSub subscription callback function to execute at new message arrival. Call function to check
            if the order is completed. If it is, change the order status to `pre-completed`.

            :param obj: Message object.
            :param update_nr: Events iterator.
            :param subscription_id: Subscription ID.
            :return: True when message got - to cancel subscription.

            """

            message = parse_income_message(obj["params"]["result"]["data"])
            id, stage_name = list(message.items())[0]
            if str(order_id) == str(id):  # TODO check the name of the stage. Check result
                hass.async_create_task(_check_result())
                hass.async_create_task(_change_stage(str(order_id), str(precompleted_stage_id)))
                return True

    @to_thread
    def _create_order(name: str) -> int:
        """Create order in Fieldservice addon in Odoo.

        :param name: Name of the order. Name of the sensor, which triggers the service.
        :return: The order id.
        """

        location_id = 2
        todo = "some job"
        worker_id = 6
        priority = "3"
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
                    "scheduled_duration": scheduled_duration,
                }
            ],
        )
        return order_id

    @to_thread
    def _change_stage(order_id: str, stage_id: str) -> int:
        """Change stage of the order to `Pre Completed` in Fieldservice addon in Odoo.

        :param order_id: The order id.
        :param stage_id: The `Pre Completed` stage id.
        """

        connection.execute_kw(
            entry.data[DB], uid, entry.data[PASSWORD], "fsm.order", "write", [[int(order_id)], {"stage_id": stage_id}]
        )

    @to_thread
    def _check_result() -> None:
        pass

    hass.services.async_register(DOMAIN, CREATE_ORDERS_SERVICE, handle_create_order)
    return True


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    return True
