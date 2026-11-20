import pytest

from redline.liner import Usage
from redline.pipeline import review


class SearchFixture:
    def __init__(self, index, excerpt, results):
        self.usage = Usage()
        self.results = results
        self.index = index
        self.excerpt = excerpt

    def chat_json(self, system, user, **kwargs):
        if "주장 원장을 만든다" in system:
            return {"claims": [{"type": "definition", "quote": "용어의 정의입니다."}]}
        if "source_index" in system:
            return {"verdict": "supported", "source_index": self.index, "evidence": self.excerpt}
        return {"findings": []}

    def search(self, *args, **kwargs):
        return self.results


SOURCE = {"url": "https://example.org/definition", "host": "example.org", "title": "정의", "snippet": "공식 정의입니다."}


@pytest.mark.parametrize("index, quote, results, expected", [
    (0, "공식 정의", [], "unverified"), (7, "공식 정의", [SOURCE], "unverified"),
    (True, "공식 정의", [SOURCE], "unverified"), (0, "없는 구절", [SOURCE], "unverified"),
    (0, "공식 정의", [SOURCE], "confirmed"),
])
def test_evidence_required_for_search_verdict(index, quote, results, expected):
    item = review("용어의 정의입니다.", liner=SearchFixture(index, quote, results))["claims"][0]
    assert item["status"] == expected
    assert bool(item["sources"]) == (expected == "confirmed")
