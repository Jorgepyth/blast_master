import pytest
import requests_mock
from tools.notion_sync import post_to_notion, RateLimitError, NotionAPIError
from tenacity import RetryError

def test_rate_limit_backoff():
    # Test that a 429 response triggers a retry and eventually fails after 5 attempts
    url = "https://api.notion.com/v1/pages"
    
    with requests_mock.Mocker() as m:
        m.post(url, status_code=429)
        
        with pytest.raises(RetryError) as exc_info:
            import tools.notion_sync
            # Override tenacity retry wait to 0 for test speed
            tools.notion_sync.post_to_notion.retry.wait = pytest.importorskip("tenacity").wait_none()
            tools.notion_sync.post_to_notion({"test": "data"})
            
        assert m.call_count == 5

def test_successful_post():
    url = "https://api.notion.com/v1/pages"
    with requests_mock.Mocker() as m:
        m.post(url, json={"id": "mock_id"}, status_code=200)
        resp = post_to_notion({"test": "data"})
        assert resp["id"] == "mock_id"
        assert m.call_count == 1
