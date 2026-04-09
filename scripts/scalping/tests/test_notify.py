from unittest.mock import MagicMock, patch

from blofin_bridge.notify import Notifier


def test_notifier_noop_when_unconfigured():
    n = Notifier(bot_token="", chat_id="")
    n.send("hello")  # should not raise, should not call httpx


def test_notifier_posts_to_telegram():
    with patch("blofin_bridge.notify.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        n = Notifier(bot_token="tok", chat_id="123")
        n.send("hello world")
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "tok" in args[0]
        assert kwargs["json"]["chat_id"] == "123"
        assert "hello world" in kwargs["json"]["text"]


def test_notifier_swallows_http_error():
    with patch("blofin_bridge.notify.httpx.post", side_effect=Exception("boom")):
        n = Notifier(bot_token="tok", chat_id="123")
        n.send("test")  # must not raise
