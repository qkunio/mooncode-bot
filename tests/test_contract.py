import unittest

from bot import contract

OLD = """# header
POST /api/v1/sessions/{session_id}:abort param path:session_id: string minLength=1
POST /api/v1/sessions/{session_id}:abort: operation
POST /api/v1/sessions/{session_id}:undo request.count: integer
POST /api/v1/sessions/{session_id}:undo request: object closed
GET /api/v1/sessions/{session_id} response 200.data.busy: boolean
GET /api/v1/sessions/{session_id} response 200.data.state: enum "a"|"b"
GET /api/v1/sessions/{session_id} response 200.data.meta: shape aaaaaaaa
GET /api/v1/providers response 200.data.x: string
ws server message usage.context_tokens: integer
shape aaaaaaaa.title: string
"""

NEW = """# header
POST /api/v1/sessions/{session_id}:cancel param path:session_id: string minLength=1
POST /api/v1/sessions/{session_id}:cancel: operation
POST /api/v1/sessions/{session_id}:undo request.turn_id: string minLength=1
POST /api/v1/sessions/{session_id}:undo request: object closed
GET /api/v1/sessions/{session_id} response 200.data.activity: enum "idle"|"turn"
GET /api/v1/sessions/{session_id} response 200.data.state: enum "a"|"b"|"c"
GET /api/v1/sessions/{session_id} response 200.data.meta: shape bbbbbbbb
GET /api/v1/providers response 200.data.y: string
ws server message context.context_tokens: integer
shape bbbbbbbb.title: string optional
"""

SURFACE = {
    "POST /api/v1/sessions/{}:abort",
    "POST /api/v1/sessions/{}:undo",
    "GET /api/v1/sessions/{}",
    "ws * usage",
}


class ContractTest(unittest.TestCase):
    def test_parse_and_op(self):
        c = contract.parse(OLD)
        self.assertEqual(c["POST /api/v1/sessions/{session_id}:abort"], "operation")
        self.assertEqual(contract.op_of("POST /api/v1/sessions/{session_id}:undo request.count"),
                         "POST /api/v1/sessions/{}:undo")
        self.assertEqual(contract.op_of("ws server message item_delta.delta"), "ws server item_delta")
        self.assertEqual(contract.op_of("ws server message session_event<config>.x"), "ws server session_event")

    def test_unchanged(self):
        rep = contract.analyze(OLD, OLD, SURFACE)
        self.assertTrue(rep.contract_unchanged)
        self.assertFalse(rep.needs_adaptation)

    def test_analyze(self):
        rep = contract.analyze(OLD, NEW, SURFACE)
        by_key = {c.key: c for c in rep.consumed}
        self.assertEqual(by_key["POST /api/v1/sessions/{session_id}:abort"].severity, "breaking")
        self.assertEqual(by_key["POST /api/v1/sessions/{session_id}:undo request.turn_id"].severity, "breaking")
        self.assertEqual(by_key["GET /api/v1/sessions/{session_id} response 200.data.busy"].severity, "likely_breaking")
        self.assertEqual(by_key["GET /api/v1/sessions/{session_id} response 200.data.activity"].severity, "additive")
        self.assertEqual(by_key["GET /api/v1/sessions/{session_id} response 200.data.state"].severity, "likely_breaking")
        self.assertIn("ws server message usage.context_tokens", by_key)
        # shape 换号：在引用处按真实字段差异定级（title 变可选 → likely_breaking），不单独列出整个 shape
        meta = by_key["GET /api/v1/sessions/{session_id} response 200.data.meta"]
        self.assertEqual(meta.severity, "likely_breaking")
        self.assertIn(".title", meta.reason)
        self.assertNotIn("shape aaaaaaaa.title", by_key)
        # 未使用的接口 → 忽略
        self.assertNotIn("GET /api/v1/providers response 200.data.x", by_key)
        self.assertIn("GET /api/v1/providers", rep.ignored_ops)
        self.assertEqual(rep.max_severity, "breaking")
        self.assertTrue(rep.needs_adaptation)

    def test_shape_swap_additive(self):
        old = "GET /api/v1/x response 200.data: shape 11111111\nshape 11111111.id: string\n"
        new = ("GET /api/v1/x response 200.data: shape 22222222\nshape 22222222.id: string\n"
               "shape 22222222.root: string optional\n")
        rep = contract.analyze(old, new, {"GET /api/v1/x"})
        self.assertEqual(len(rep.consumed), 1)
        self.assertEqual(rep.consumed[0].severity, "additive")
        self.assertIn("结构等价扩展", rep.consumed[0].reason)
        self.assertFalse(rep.needs_adaptation)

    def test_route_match(self):
        self.assertTrue(contract.route_match("/api/v1/sessions/{}/agents/{}/turns", "/api/v1/sessions/{}/agents/main/turns"))
        self.assertFalse(contract.route_match("/api/v1/sessions/{}:fork", "/api/v1/sessions/{}"))
        self.assertTrue(contract.route_match("/api/v1/sessions/{}:undo", "/api/v1/sessions/{}:undo"))

    def test_rename_candidate(self):
        old = "ws server message context.context_tokens: integer\n"
        new = "ws server message ctx.context_tokens: integer\n"
        rep = contract.analyze(old, new, {"ws * context"})
        ops = {c.op: c for c in rep.consumed}
        self.assertEqual(ops["ws server context"].severity, "breaking")
        self.assertIn("ws server ctx", ops)
        self.assertIn("替代", ops["ws server ctx"].reason)

    def test_additive_only_is_compatible(self):
        new = OLD + "GET /api/v1/sessions/{session_id} response 200.data.extra: string optional\n"
        rep = contract.analyze(OLD, new, SURFACE)
        self.assertFalse(rep.needs_adaptation)
        self.assertEqual(rep.max_severity, "additive")


if __name__ == "__main__":
    unittest.main()
