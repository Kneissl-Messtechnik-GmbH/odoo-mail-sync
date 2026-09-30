# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import json
from unittest import mock

from odoo.tests.common import BaseCase

from ..services import graph


class _Response:
    def __init__(self, status, payload=None, headers=None, text=""):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def iter_content(self, chunk_size=1):
        yield self.text.encode()


class TestGraphClient(BaseCase):
    def _client(self, responses):
        session = mock.Mock()
        session.request = mock.Mock(side_effect=responses)
        return graph.GraphClient(graph.StaticTokenProvider("tok"), session=session), session

    def test_headers_and_paging(self):
        client, session = self._client(
            [
                _Response(200, {"value": [{"id": "1"}], "@odata.nextLink": "https://g/next"}),
                _Response(200, {"value": [{"id": "2"}], "@odata.deltaLink": "https://g/delta"}),
            ]
        )
        rows, link = client.delta("u@x.de", "f1")
        self.assertEqual([r["id"] for r in rows], ["1", "2"])
        self.assertEqual(link, "https://g/delta")
        headers = session.request.call_args_list[0].kwargs["headers"]
        self.assertIn('IdType="ImmutableId"', headers["Prefer"])
        self.assertEqual(headers["Authorization"], "Bearer tok")
        self.assertEqual(session.request.call_args_list[1].args[1], "https://g/next")

    def test_throttled(self):
        client, _ = self._client([_Response(429, {"error": {"code": "TooMany"}}, headers={"Retry-After": "17"})])
        with self.assertRaises(graph.GraphThrottled) as ctx:
            client.delta("u@x.de", "f1")
        self.assertEqual(ctx.exception.retry_after, 17)

    def test_gone_and_unauthorized(self):
        client, _ = self._client([_Response(410, {"error": {"code": "syncStateNotFound", "message": "x"}})])
        with self.assertRaises(graph.GraphGone):
            client.delta("u@x.de", "f1", delta_link="https://g/delta")
        client, session = self._client(
            [_Response(401, {"error": {"code": "InvalidAuthenticationToken"}}), _Response(401, {"error": {}})]
        )
        with self.assertRaises(graph.GraphUnauthorized):
            client.organization()
        self.assertEqual(session.request.call_count, 2)  # one retry after token invalidation

    def test_folders_recursive(self):
        well_known = [_Response(200, {"id": "in"}), _Response(200, {"id": "sent"})] + [
            _Response(404, {"error": {"code": "ErrorItemNotFound"}}) for _ in graph.WELL_KNOWN_FOLDERS[2:]
        ]
        client, _ = self._client(
            well_known
            + [
                _Response(
                    200,
                    {
                        "value": [
                            {"id": "in", "displayName": "Inbox", "childFolderCount": 1},
                            {"id": "sent", "displayName": "Sent", "childFolderCount": 0},
                        ]
                    },
                ),
                _Response(200, {"value": [{"id": "sub", "displayName": "Kunden", "childFolderCount": 0}]}),
            ]
        )
        folders = client.list_folders("u@x.de")
        self.assertEqual([f["path"] for f in folders], ["Inbox", "Inbox/Kunden", "Sent"])
        self.assertEqual(folders[0]["wellKnownName"], "inbox")
        self.assertEqual(folders[2]["wellKnownName"], "sentitems")

    def test_mime(self):
        client, _ = self._client([_Response(200, text="From: a@b.c\r\n\r\nhi")])
        self.assertEqual(client.mime("u@x.de", "m1"), b"From: a@b.c\r\n\r\nhi")
