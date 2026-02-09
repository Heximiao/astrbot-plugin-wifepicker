from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core.onebot_gateway import OneBotGateway, unwrap_onebot_data


class _DummyApi:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def call_action(self, action: str, **payload):
        self.calls.append((action, payload))
        return self.responses.get(action)


class _DummyBot:
    def __init__(self, api):
        self.api = api


class _DummyEvent:
    def __init__(self, api, *, group_id="123", sender_id="456", message_str=""):
        self.bot = _DummyBot(api)
        self._group_id = group_id
        self._sender_id = sender_id
        self.message_str = message_str
        self.message_obj = type("MsgObj", (), {"message": []})()

    def get_group_id(self):
        return self._group_id

    def get_sender_id(self):
        return self._sender_id


class _DummyAt:
    def __init__(self, qq):
        self.qq = qq


class OneBotGatewayTest(unittest.IsolatedAsyncioTestCase):
    def test_unwrap_onebot_data(self) -> None:
        self.assertEqual(unwrap_onebot_data({"data": [1, 2]}), [1, 2])
        self.assertEqual(unwrap_onebot_data([1, 2]), [1, 2])

    async def test_send_message_extracts_message_id(self) -> None:
        api = _DummyApi(
            {
                "send_group_msg": {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": 77},
                }
            }
        )
        event = _DummyEvent(api)
        gateway = OneBotGateway()

        message_id = await gateway.send_message(
            event,
            message=[{"type": "text", "data": {"text": "hello"}}],
        )

        self.assertEqual(message_id, 77)
        self.assertEqual(api.calls[0][0], "send_group_msg")

    async def test_fetch_group_member_list_supports_data_wrapper(self) -> None:
        api = _DummyApi(
            {
                "get_group_member_list": {
                    "status": "ok",
                    "data": [{"user_id": 1, "nickname": "a"}],
                }
            }
        )
        event = _DummyEvent(api)
        gateway = OneBotGateway()

        members = await gateway.fetch_group_member_list(event)
        self.assertEqual(len(members), 1)
        self.assertEqual(str(members[0]["user_id"]), "1")

    async def test_fetch_group_info_supports_data_wrapper(self) -> None:
        api = _DummyApi(
            {
                "get_group_info": {
                    "status": "ok",
                    "data": {"group_name": "Test Group"},
                }
            }
        )
        event = _DummyEvent(api)
        gateway = OneBotGateway()

        info = await gateway.fetch_group_info(event)
        self.assertEqual(info["group_name"], "Test Group")

    def test_extract_target_id_from_message(self) -> None:
        event = _DummyEvent(_DummyApi({}), message_str="强娶 [CQ:at,qq=123456]")
        self.assertEqual(OneBotGateway.extract_target_id_from_message(event), "123456")

        event2 = _DummyEvent(_DummyApi({}), message_str="强娶 @123456")
        self.assertEqual(OneBotGateway.extract_target_id_from_message(event2), "123456")

        event3 = _DummyEvent(_DummyApi({}), message_str="")
        event3.message_obj.message = [_DummyAt(7890)]
        self.assertEqual(OneBotGateway.extract_target_id_from_message(event3), "7890")


if __name__ == "__main__":
    unittest.main()
