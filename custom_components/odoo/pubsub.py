"""Robonomics PubSub functionality implementation."""

import asyncio
import functools
import typing as tp
import logging
from ast import literal_eval
from robonomicsinterface import Account, PubSub

from .const import ROBONOMICS_NODE_MULTIADDR, ROBONOMICS_NODE

_LOGGER = logging.getLogger(__name__)


def to_thread(func: tp.Callable) -> tp.Coroutine:
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        return await asyncio.to_thread(func, *args, **kwargs)

    return wrapper


def parse_income_message(raw_data: tp.List[tp.Any]) -> dict:
    """
    Parse income PubSub Message.
    :param raw_data: Income PubSub Message.
    :return: {order_id: stage_name}
    """

    for i in range(len(raw_data)):
        raw_data[i] = chr(raw_data[i])
    data = "".join(raw_data)
    data_dict = literal_eval(data)

    return data_dict


@to_thread
def subscribe_response_topic(response_topic, callback):
    """
    Subscribe to a query response topic in Robonomics Pubsub.
    :param response_topic: Topic in PubSub to subscribe to.
    :param callback: Callback function to execute when new message registered.
    """
    account_ = Account(remote_ws=ROBONOMICS_NODE)
    pubsub_ = PubSub(account_)
    _LOGGER.debug(f"Subscribing to topic '{response_topic}'")
    pubsub_.subscribe(response_topic, result_handler=callback)


async def subscribe_response_topic_wrapper(response_topic, callback, timeout):
    """
    Timeout wrapper for PubSub subscription function.
    :param response_topic: Topic in PubSub to subscribe to.
    :param callback: Callback function to execute when new message registered.
    :param timeout: Timeout to cancel subscription if no response got via PubSub.
    """
    await asyncio.wait_for(subscribe_response_topic(response_topic, callback), timeout=timeout)
