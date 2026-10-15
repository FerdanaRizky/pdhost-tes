import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import agent


class FileManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_root = agent.SITE_ROOT
        agent.SITE_ROOT = self.temp.name
        self.public = Path(self.temp.name) / "example.com" / "public"
        self.public.mkdir(parents=True)
        (self.public / "index.html").write_text("hello", encoding="utf-8")

    def tearDown(self):
        agent.SITE_ROOT = self.old_root
        self.temp.cleanup()

    def test_list_and_read(self):
        listing = agent.list_site_files("example.com")
        self.assertTrue(listing["ok"])
        self.assertEqual(listing["items"][0]["name"], "index.html")
        result = agent.read_site_file("example.com", "index.html")
        self.assertEqual(result["content"], "hello")

    def test_write_create_and_delete_file(self):
        created = agent.write_site_file("example.com", "about.txt", "first", create_only=True)
        self.assertTrue(created["ok"])
        with self.assertRaises(ValueError):
            agent.write_site_file("example.com", "about.txt", "duplicate", create_only=True)
        agent.write_site_file("example.com", "about.txt", "updated")
        self.assertEqual(agent.read_site_file("example.com", "about.txt")["content"], "updated")
        agent.delete_site_entry("example.com", "about.txt")
        self.assertFalse((self.public / "about.txt").exists())

    def test_directory_operations(self):
        agent.create_site_directory("example.com", "assets")
        self.assertTrue((self.public / "assets").is_dir())
        agent.delete_site_entry("example.com", "assets")
        self.assertFalse((self.public / "assets").exists())

    def test_reject_path_traversal(self):
        for path in ("../outside.txt", "sub/../../outside.txt", "/etc/passwd", r"..\outside.txt"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    agent.resolve_site_path("example.com", path)

    def test_reject_symlink(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        (self.public / "shortcut").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            agent.resolve_site_path("example.com", "shortcut/secret.txt")
        self.assertNotIn("shortcut", [x["name"] for x in agent.list_site_files("example.com")["items"]])

    def test_reject_invalid_domain(self):
        with self.assertRaises(ValueError):
            agent.resolve_site_path("example.com/../../etc", "passwd")


if __name__ == "__main__":
    unittest.main()
