import json
import tempfile
import unittest
from pathlib import Path

from bot import surface
from bot.context.budget import estimate_tokens, truncate_to_tokens
from bot.context.logs import digest_build_log
from bot.context.pack import ContextPack
from bot.kimi import _cli_prompt
from bot.pipeline import format_review_diffs
from bot.target import next_version

SWIFT = r'''
func abort(_ id: String) async throws {
    let _: JSONValue = try await post("sessions/\(id):cancel", body: Empty())
    let _: JSONValue = try await send(method: "PUT", path: "providers/\(pid)", query: [:], body: body)
    var request = URLRequest(url: endpoint.apiBaseURL.appending(path: "files"))
}
switch type {
case "turn.started", "turn.ended": break
case "item.delta": break
}
'''


class SurfaceTest(unittest.TestCase):
    def test_scan(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "KimiCode" / "Core"
            p.mkdir(parents=True)
            (p / "KapClient.swift").write_text(SWIFT)
            data = surface.scan(Path(d), ["KimiCode/Core/*.swift"], extra=["ws client pong"])
            ops = surface.ops_set(data)
            self.assertIn("POST /api/v1/sessions/{}:cancel", ops)
            self.assertIn("PUT /api/v1/providers/{}", ops)
            self.assertIn("* /api/v1/files", ops)
            self.assertIn("ws * turn", ops)
            self.assertIn("ws * item_delta", ops)
            self.assertIn("ws client pong", ops)
            ptr = surface.pointers_for(data, ["POST /api/v1/sessions/{}:cancel", "ws server item_delta"])
            self.assertTrue(ptr["POST /api/v1/sessions/{}:cancel"][0].startswith("KimiCode/Core/KapClient.swift:"))
            self.assertIn("ws server item_delta", ptr)


class ContextTest(unittest.TestCase):
    def test_estimate(self):
        self.assertEqual(estimate_tokens("中文"), 2)
        self.assertEqual(estimate_tokens("abcdefgh"), 2)

    def test_truncate(self):
        text = "\n".join(f"line {i}" for i in range(2000))
        out = truncate_to_tokens(text, 100, "see file")
        self.assertLess(estimate_tokens(out), 130)
        self.assertIn("已截断", out)

    def test_pack_priority_and_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            pack = ContextPack("t", budget=300)
            pack.add("任务", "做一件事", 0)
            pack.add("低优先级大段", "x" * 4000, 9, source="/tmp/big.txt", min_tokens=50)
            pack.add("高优先级", "y" * 400, 1)
            text = pack.render(Path(d))
            self.assertIn("做一件事", text)
            self.assertIn("y" * 400, text)
            self.assertIn("已截断", text)
            # 渲染顺序保持添加顺序
            self.assertLess(text.index("任务"), text.index("低优先级大段"))
            self.assertTrue((Path(d) / "manifest.json").exists())

    def test_pack_drop_leaves_pointer(self):
        pack = ContextPack("t", budget=60)
        pack.add("任务", "a" * 200, 0)
        pack.add("大段", "b" * 4000, 9, source="/tmp/big.txt", min_tokens=500)
        text = pack.render()
        self.assertIn("/tmp/big.txt", text)
        self.assertNotIn("b" * 100, text)

    def test_pack_unlimited(self):
        with tempfile.TemporaryDirectory() as d:
            pack = ContextPack("review", budget=None)
            big = "x" * 20000
            pack.add("任务", "审查", 0)
            pack.add("完整 diff", big, 2)
            text = pack.render(Path(d))
            self.assertIn("budget=unlimited", text)
            self.assertIn(big, text)
            self.assertNotIn("已截断", text)
            manifest = json.loads((Path(d) / "manifest.json").read_text("utf-8"))
            self.assertIsNone(manifest["budget"])
            self.assertGreater(manifest["used"], 5000)
            self.assertTrue(all(s["status"] == "full" for s in manifest["sections"]))

    def test_format_review_diffs(self):
        big_swift = "diff --git a/KimiCode/Core/Conversation.swift b/KimiCode/Core/Conversation.swift\n" + "+let a = 1\n" * 3000
        big_json = "diff --git a/reference/openapi.json b/reference/openapi.json\n" + '+"k": "v",\n' * 3000
        binary = "diff --git a/assets/icon.png b/assets/icon.png\nBinary files a/assets/icon.png and b/assets/icon.png differ\n"
        parts = format_review_diffs(big_swift + big_json + binary, per_file=None)
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0], big_swift)  # 代码文件完整保留
        self.assertIn("大体积数据文件", parts[1])
        self.assertIn("+3000/-0", parts[1])
        self.assertIn("二进制/资源文件", parts[2])

    def test_cli_prompt_fallback(self):
        p = Path("/tmp/test.prompt.md")
        self.assertEqual(_cli_prompt("short", p, max_bytes=100), "short")
        big = "a" * 200
        fallback = _cli_prompt(big, p, max_bytes=100)
        self.assertIn("/tmp/test.prompt.md", fallback)
        self.assertIn("Read", fallback)

    def test_log_digest(self):
        log = "\n".join(["noise"] * 5000 + [
            "/w/KimiCode/Core/KapClient.swift:12:5: error: cannot find 'x' in scope",
            "    x()",
            "/w/KimiCode/Core/KapClient.swift:12:5: error: cannot find 'x' in scope",
            "** BUILD FAILED **",
        ])
        d = digest_build_log(log, 2000, "/w")
        self.assertIn("KimiCode/Core/KapClient.swift:12:5: error", d)
        self.assertEqual(d.count("cannot find 'x'"), 1)
        self.assertNotIn("noise", d)


class VersionTest(unittest.TestCase):
    def test_next_version(self):
        self.assertEqual(next_version(None, "1.10"), "1.10")
        self.assertEqual(next_version("1.10", "1.10"), "1.11")
        self.assertEqual(next_version("1.8", "1.10"), "1.10")
        self.assertEqual(next_version("2.0.1", "1.10"), "2.1")


if __name__ == "__main__":
    unittest.main()
