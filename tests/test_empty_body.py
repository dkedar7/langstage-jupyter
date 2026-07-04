"""Empty POST body returns 400, not a 500 on None.get() (gh #53).

Jupyter Server's get_json_body() returns None for an EMPTY body (it only raises
on invalid JSON), so ChatHandler/ResumeHandler/CancelHandler used to hit
None.get(...) -> AttributeError -> HTTP 500. They now guard and raise HTTPError(400).
"""

import asyncio
from unittest.mock import MagicMock

import pytest
from tornado.web import Application, HTTPError

from langstage_jupyter.handlers import CancelHandler, ChatHandler, ResumeHandler


def _handler(cls):
    h = cls(Application(), MagicMock())
    h._jupyter_current_user = "test"  # satisfy @web.authenticated without real auth
    h._current_user = "test"  # tornado's cache, so current_user won't call get_current_user
    h.get_json_body = lambda: None  # simulate an empty request body
    return h


@pytest.mark.parametrize("cls", [ChatHandler, ResumeHandler, CancelHandler])
def test_empty_body_is_400_not_500(cls):
    h = _handler(cls)
    with pytest.raises(HTTPError) as exc:
        asyncio.run(h.post())
    assert exc.value.status_code == 400, f"{cls.__name__} should 400 on an empty body"
