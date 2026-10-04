from app.tools import weather as weather_module


class FakeResponse:
    def __init__(self, results):
        self._results = results

    def raise_for_status(self):
        return None

    def json(self):
        return {"results": self._results}


def test_get_coordinates_prefers_chinese_city_suffix(monkeypatch):
    requested_names = []

    def fake_get(url, params, timeout):
        requested_names.append(params["name"])
        return FakeResponse(
            [
                {
                    "name": "长沙市",
                    "country": "中国",
                    "admin1": "湖南",
                    "latitude": 28.19874,
                    "longitude": 112.97087,
                    "timezone": "Asia/Shanghai",
                }
            ]
        )

    monkeypatch.setattr(weather_module.httpx, "get", fake_get)

    location = weather_module.get_coordinates("长沙")

    assert requested_names == ["长沙市"]
    assert location["name"] == "长沙市"
    assert location["admin1"] == "湖南"
    assert location["latitude"] == 28.19874
    assert location["longitude"] == 112.97087


def test_get_coordinates_falls_back_to_original_name(monkeypatch):
    requested_names = []

    def fake_get(url, params, timeout):
        requested_names.append(params["name"])
        if params["name"] == "香港市":
            return FakeResponse([])
        return FakeResponse(
            [
                {
                    "name": "香港",
                    "country": "中国",
                    "admin1": None,
                    "latitude": 22.27832,
                    "longitude": 114.17469,
                    "timezone": "Asia/Hong_Kong",
                }
            ]
        )

    monkeypatch.setattr(weather_module.httpx, "get", fake_get)

    location = weather_module.get_coordinates("香港")

    assert requested_names == ["香港市", "香港"]
    assert location["name"] == "香港"
