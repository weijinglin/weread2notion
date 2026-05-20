import importlib.util
import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "weread.py"


class FakePages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": "page-id"}


class FakeClient:
    def __init__(self):
        self.pages = FakePages()


class FakeWeRead:
    def __init__(self, responses):
        self.responses = list(responses)

    def request(self, api_name, **kwargs):
        if not self.responses:
            raise AssertionError(f"unexpected request: {api_name}")
        return self.responses.pop(0)


def load_weread_module():
    sys.modules.setdefault("notion_client", types.SimpleNamespace(Client=object))
    sys.modules.setdefault("requests", types.SimpleNamespace(Session=object))
    sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda: None))
    sys.modules.setdefault(
        "retrying", types.SimpleNamespace(retry=lambda *args, **kwargs: (lambda fn: fn))
    )
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("weread_module", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SyncSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.weread = load_weread_module()

    def test_default_sync_includes_books_older_than_latest_sort(self):
        books = [
            {"sort": 10, "book": {"bookId": "older"}},
            {"sort": 20, "book": {"bookId": "newer"}},
        ]

        selected = self.weread.select_books_to_sync(books, latest_sort=20)

        self.assertEqual([book["book"]["bookId"] for book in selected], ["older", "newer"])

    def test_incremental_sync_only_keeps_books_newer_than_latest_sort(self):
        books = [
            {"sort": 10, "book": {"bookId": "older"}},
            {"sort": 30, "book": {"bookId": "newer"}},
        ]

        selected = self.weread.select_books_to_sync(
            books, latest_sort=20, incremental=True
        )

        self.assertEqual([book["book"]["bookId"] for book in selected], ["newer"])

    def test_get_bookmark_list_fetches_all_pages(self):
        self.weread.weread = FakeWeRead(
            [
                {
                    "hasMore": 1,
                    "synckey": 100,
                    "updated": [{"range": "11-12"}, {"range": "01-02"}],
                },
                {
                    "hasMore": 0,
                    "synckey": 200,
                    "updated": [{"range": "21-22"}],
                },
            ]
        )

        bookmarks = self.weread.get_bookmark_list("book-id")

        self.assertEqual([item["range"] for item in bookmarks], ["01-02", "11-12", "21-22"])

    def test_get_review_list_uses_expected_review_count_as_request_count(self):
        captured = {}

        class CapturingWeRead(FakeWeRead):
            def request(self, api_name, **kwargs):
                captured["api_name"] = api_name
                captured["kwargs"] = kwargs
                return super().request(api_name, **kwargs)

        self.weread.weread = CapturingWeRead(
            [
                {
                    "hasMore": 0,
                    "synckey": 123,
                    "reviews": [
                        {"review": {"type": 1, "content": "note one", "chapterUid": 1}},
                    ],
                }
            ]
        )

        self.weread.get_review_list("book-id", expected_count=159)

        self.assertEqual(captured["api_name"], "/review/list/mine")
        self.assertEqual(captured["kwargs"]["count"], 159)

    def test_get_review_list_keeps_all_non_summary_types(self):
        self.weread.weread = FakeWeRead(
            [
                {
                    "hasMore": 0,
                    "synckey": 123,
                    "reviews": [
                        {"review": {"type": 1, "content": "note one", "chapterUid": 1}},
                        {"review": {"type": 2, "content": "note two", "chapterUid": 1}},
                        {"review": {"type": 4, "content": "summary"}},
                    ],
                }
            ]
        )

        summary, reviews = self.weread.get_review_list("book-id", expected_count=100)

        self.assertEqual([item["noteType"] for item in summary], [4])
        self.assertEqual([item["noteType"] for item in reviews], [1, 2])
        self.assertEqual([item["markText"] for item in reviews], ["note one", "note two"])

    def test_get_children_appends_type_and_abstract_after_note(self):
        chapter = {1: {"chapterUid": 1, "chapterIdx": 1, "level": 1, "title": "Ch1"}}
        summary = []
        bookmark_list = [
            {
                "chapterUid": 1,
                "markText": "正文",
                "noteType": 2,
                "abstract": "摘录",
            }
        ]

        children, grandchild = self.weread.get_children(chapter, summary, bookmark_list)

        self.assertEqual(children[0]["type"], "heading_1")
        self.assertEqual(children[1]["callout"]["rich_text"][0]["text"]["content"], "正文")
        self.assertEqual(
            grandchild[1]["quote"]["rich_text"][0]["text"]["content"],
            "摘录",
        )

    def test_expected_review_count_should_come_from_notebook_item_not_nested_book(self):
        notebook_item = {
            "reviewCount": 159,
            "book": {"bookId": "book-id", "title": "Book"},
        }

        expected_review_count = self.weread.get_expected_review_count(notebook_item)

        self.assertEqual(expected_review_count, 159)

    def test_insert_to_notion_writes_note_count_property(self):
        self.weread.client = FakeClient()
        self.weread.database_id = "database-id"
        self.weread.get_read_info = lambda bookId: None

        self.weread.insert_to_notion(
            "Book",
            "book-id",
            "https://example.com/cover.jpg",
            1,
            "Author",
            "isbn",
            4.5,
            ["分类"],
            7,
        )

        properties = self.weread.client.pages.calls[0]["properties"]
        self.assertEqual(properties["NoteCount"]["number"], 7)


if __name__ == "__main__":
    unittest.main()
