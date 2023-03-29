import asyncio
import functools
import xmlrpc.client
import typing as tp
import time
import logging
from datetime import datetime
from .const import DOMAIN, CREATE_ORDERS_SERVICE, DB, HOST, PORT, USERNAME, PASSWORD
from .pubsub import subscribe_response_topic, parse_income_message
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.typing import ConfigType
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_state_change_event,
)

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
    onapprove_stage_id = 4
    approved_stage_id = 8

    async def handle_create_order(call: ServiceCall) -> None:
        """Callback for create_order service"""

        name = call.data["name"]
        _LOGGER.debug(f"Name from service: {name}")
        order_id = await _create_order(name)
        topic = f"odoo_change_order_stage_{order_id}"
        sensor_id = call.data["sensor_id"]
        _LOGGER.debug(f"Sensor id in handle create order: {sensor_id}")

        def _pubsub_callback(obj, update_nr, subscription_id) -> bool:
            """PubSub subscription callback function to execute at new message arrival. Call function to check
            if the order is completed. If it is, change the order status to `pre-completed`.

            :param obj: Message object.
            :param update_nr: Events iterator.
            :param subscription_id: Subscription ID.
            :return: True when message got - to cancel subscription.

            """

            message = parse_income_message(obj["params"]["result"]["data"])
            _LOGGER.debug(f"Got msg from topic: {message}")
            id, stage_name = list(message.items())[0]
            _LOGGER.debug(f"Order id: {id}")
            _LOGGER.debug(f"Stage name: {stage_name}")
            if (str(order_id) == str(id)) and (stage_name == "Completed"):
                hass.async_create_task(_change_stage(int(order_id), int(onapprove_stage_id)))
                if _check_result(sensor_id):
                    hass.async_create_task(_change_stage(int(order_id), int(approved_stage_id)))
                    return True

        resp_sub = asyncio.ensure_future(subscribe_response_topic(topic, _pubsub_callback))

    @to_thread
    def _create_order(name: str) -> int:
        """Create order in Fieldservice addon in Odoo.

        :param name: Name of the order. Name of the sensor, which triggers the service.
        :return: The order id.
        """

        location_id = 1
        timestamp = time.strftime("%d.%m.%Y, %H:%M", time.localtime())
        todo = "some job"
        worker_id = 1
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
                    "name": f"{name} {timestamp}",
                    "scheduled_date_start": scheduled_date_start,
                    "scheduled_duration": scheduled_duration,
                }
            ],
        )
        return order_id

    @to_thread
    def _change_stage(order_id: int, stage_id: int) -> int:
        """Change stage of the order to `Pre Completed` in Fieldservice addon in Odoo.

        :param order_id: The order id.
        :param stage_id: New stage id.
        """

        connection.execute_kw(
            entry.data[DB], uid, entry.data[PASSWORD], "fsm.order", "write", [[order_id], {"stage_id": stage_id}]
        )
        _LOGGER.debug(f"Stage for order {order_id} is updated")

    def _check_result(sensor_id: str) -> bool:
        sensor_state = hass.states.get(sensor_id).state
        _LOGGER.debug(f"Sensor state on Completed stage: {sensor_state}")
        time.sleep(10)
        return sensor_state == "off"

    def _callback_state_change(event):
        """Callback for sensor's state changing. Calls `create_order` service.
        :param event: Home Assistant event.
        """
        sensor_id = event.data.get("entity_id")
        new_state = event.data.get("new_state").state
        _LOGGER.debug(f"New state of the sensor: {new_state}")
        if new_state == "on":
            entity = entity_registry.entities[sensor_id]
            sensor_type = entity.original_device_class
            sensor_name = entity.name
            _LOGGER.debug(f"Name of the sensors: {sensor_name}, type: {sensor_type}, id {sensor_id}")
            hass.async_create_task(
                hass.services.async_call(
                    DOMAIN,
                    CREATE_ORDERS_SERVICE,
                    service_data={"name": f"{sensor_type} by {sensor_name}", "sensor_id": sensor_id},
                )
            )

    await asyncio.sleep(60)
    entity_registry = er.async_get(hass)
    sensors_types_to_track = ["moisture", "gas"]  # types of the sensors which will be tracked to create orders
    sensors_ids_to_track = []
    for entity in entity_registry.entities:
        entity_data = entity_registry.async_get(entity)
        id = entity_data.entity_id
        # _LOGGER.debug(f"device class: {entity_registry.entities[id]}")
        #  _LOGGER.debug(f"device class: {entity_registry.entities[id].device_class}")
        entity_state = hass.states.get(entity)
        if entity_state != None:
            try:
                sensor_type = str(entity_state.attributes.get("device_class"))
                if sensor_type in sensors_types_to_track:
                    if "friendly_name" in entity_state.attributes:
                        sensor_name = str(entity_state.attributes.get("friendly_name"))
                    else:
                        sensor_name = str(entity_state.attributes.get("device_class"))
                    entity_data = entity_registry.async_get(entity)
                    id = entity_data.entity_id
                    sensors_ids_to_track.append(id)
            except Exception as e:
                _LOGGER.error(f"Could not get entities with error: {e}")
                pass
    _LOGGER.debug(f"Sensors ids for tracking: {sensors_ids_to_track}")
    async_track_state_change_event(hass, sensors_ids_to_track, _callback_state_change)
    hass.services.async_register(DOMAIN, CREATE_ORDERS_SERVICE, handle_create_order)
    return True


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    return True
