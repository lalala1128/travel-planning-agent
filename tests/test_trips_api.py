def test_trip_crud(client):
    payload = {
        "destination": "成都",
        "start_date": "2026-10-01",
        "days": 3,
        "budget": 3000,
        "interests": ["美食", "摄影"],
    }

    created = client.post("/trips", json=payload)
    assert created.status_code == 201
    trip_id = created.json()["id"]

    detail = client.get(f"/trips/{trip_id}")
    assert detail.status_code == 200
    assert detail.json()["destination"] == "成都"
    assert detail.json()["plan"] is None

    listed = client.get("/trips", params={"destination": "成"})
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    updated = client.patch(
        f"/trips/{trip_id}/status",
        json={"status": "planning"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "planning"

    deleted = client.delete(f"/trips/{trip_id}")
    assert deleted.status_code == 204
    assert client.get(f"/trips/{trip_id}").status_code == 404

def test_new_trip_has_empty_plan_history(client):
    created = client.post(
        "/trips",
        json={
            "destination": "长沙",
            "start_date": "2026-10-10",
            "days": 3,
            "budget": 3000,
            "interests": ["美食", "人文"],
        },
    )
    trip_id = created.json()["id"]

    response = client.get(f"/trips/{trip_id}/plans")

    assert response.status_code == 200
    assert response.json() == []
def test_create_trip_rejects_invalid_days(client):
    response = client.post(
        "/trips",
        json={
            "destination": "成都",
            "start_date": "2026-10-01",
            "days": 0,
            "budget": 3000,
            "interests": [],
        },
    )

    assert response.status_code == 422
def test_generate_plan_rejects_duplicate_planning(
    client,
    monkeypatch,
):
    import app.routers.trips as trips_router

    created = client.post(
        "/trips",
        json={
            "destination": "长沙",
            "start_date": "2026-10-10",
            "days": 3,
            "budget": 3000,
            "interests": ["美食"],
        },
    )
    trip_id = created.json()["id"]

    status_response = client.patch(
        f"/trips/{trip_id}/status",
        json={"status": "planning"},
    )
    assert status_response.status_code == 200

    def fail_if_called(*args, **kwargs):
        raise AssertionError("重复任务不应该启动Agent")

    monkeypatch.setattr(
        trips_router,
        "run_travel_graph",
        fail_if_called,
    )
    monkeypatch.setattr(
        trips_router,
        "stream_travel_graph",
        fail_if_called,
    )

    normal_response = client.post(
        f"/trips/{trip_id}/generate-plan"
    )
    stream_response = client.post(
        f"/trips/{trip_id}/generate-plan/stream"
    )

    assert normal_response.status_code == 409
    assert stream_response.status_code == 409
    assert normal_response.json()["detail"] == (
        "Trip planning already in progress"
    )
    assert stream_response.json()["detail"] == (
        "Trip planning already in progress"
    )

    executions = client.get(
        f"/trips/{trip_id}/executions"
    ).json()

    assert executions == []
def test_stale_planning_execution_is_released(
    client,
    db_session,
):
    from datetime import datetime, timedelta

    from app.models import TripExecution
    from app.routers.trips import (
        acquire_trip_planning_lock,
    )

    created = client.post(
        "/trips",
        json={
            "destination": "长沙",
            "start_date": "2026-10-10",
            "days": 3,
            "budget": 3000,
            "interests": ["美食"],
        },
    )
    trip_id = created.json()["id"]

    client.patch(
        f"/trips/{trip_id}/status",
        json={"status": "planning"},
    )

    old_execution = TripExecution(
        trip_id=trip_id,
        mode="stream",
        status="planning",
        current_node="route",
        started_at=(
            datetime.now() - timedelta(minutes=31)
        ),
    )
    db_session.add(old_execution)
    db_session.commit()
    db_session.refresh(old_execution)

    trip = acquire_trip_planning_lock(
        db_session,
        trip_id,
    )
    db_session.commit()
    db_session.refresh(old_execution)

    assert trip.status == "planning"
    assert old_execution.status == "failed"
    assert old_execution.current_node == "failed"
    assert "已自动释放" in old_execution.error
    assert old_execution.completed_at is not None
def test_stream_plan_updates_database(client, monkeypatch):
    from app.schemas import GeneratedPlan
    import app.routers.trips as trips_router

    created = client.post(
        "/trips",
        json={
            "destination": "成都",
            "start_date": "2026-10-01",
            "days": 1,
            "budget": 1000,
            "interests": ["美食"],
        },
    )
    trip_id = created.json()["id"]
    plan = GeneratedPlan.model_validate(
        {
            "destination": "成都",
            "summary": "成都一日游",
            "total_estimated_cost": 300,
            "days": [
                {
                    "day": 1,
                    "date": "2026-10-01",
                    "weather": "晴",
                    "theme": "城市体验",
                    "daily_estimated_cost": 300,
                    "activities": [
                        {
                            "time": "09:00",
                            "place": "人民公园",
                            "activity": "喝茶",
                            "transport": "地铁",
                            "distance_from_previous_km": 0,
                            "estimated_travel_minutes": 0,
                            "estimated_cost": 50,
                        },
                        {
                            "time": "12:00",
                            "place": "宽窄巷子",
                            "activity": "午餐",
                            "transport": "步行",
                            "distance_from_previous_km": 2.5,
                            "estimated_travel_minutes": 25,
                            "estimated_cost": 100,
                        },
                        {
                            "time": "15:00",
                            "place": "武侯祠",
                            "activity": "参观",
                            "transport": "公交",
                            "distance_from_previous_km": 4,
                            "estimated_travel_minutes": 35,
                            "estimated_cost": 150,
                        },
                    ],
                }
            ],
        }
    )
    stream_state = {"count": 0}

    def fake_stream(trip):
        stream_state["count"] += 1

        current_plan = plan.model_copy(deep=True)
        current_plan.summary = (
            f"成都一日游版本{stream_state['count']}"
        )

        yield {
            "node": "weather",
            "message": "天气查询完成",
            "attempts": 0,
            "error": None,
        }
        yield {
            "node": "complete",
            "message": "旅行计划生成成功",
            "attempts": 1,
            "error": None,
            "plan": current_plan,
        }

    monkeypatch.setattr(
        trips_router,
        "stream_travel_graph",
        fake_stream,
    )

    with client.stream(
        "POST",
        f"/trips/{trip_id}/generate-plan/stream",
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: progress" in body
    assert "event: complete" in body

    detail = client.get(f"/trips/{trip_id}").json()
    assert detail["status"] == "completed"
    assert detail["plan"]["destination"] == "成都"

    task = client.get(f"/trips/{trip_id}/task").json()
    assert task["status"] == "completed"
    history_response = client.get(
        f"/trips/{trip_id}/plans"
    )

    assert history_response.status_code == 200

    history = history_response.json()

    assert len(history) == 1
    assert history[0]["trip_id"] == trip_id
    assert history[0]["version"] == 1
    assert history[0]["plan"]["destination"] == "成都"
    with client.stream(
        "POST",
        f"/trips/{trip_id}/generate-plan/stream",
    ) as second_response:
        second_body = "".join(second_response.iter_text())

    assert second_response.status_code == 200
    assert "event: complete" in second_body

    updated_history = client.get(
        f"/trips/{trip_id}/plans"
    ).json()

    assert len(updated_history) == 2
    assert [
        item["version"]
        for item in updated_history
    ] == [2, 1]
    assert (
        updated_history[0]["plan"]["summary"]
        == "成都一日游版本2"
    )
    assert (
        updated_history[1]["plan"]["summary"]
        == "成都一日游版本1"
    )

    restore_response = client.post(
        f"/trips/{trip_id}/plans/1/restore"
    )

    assert restore_response.status_code == 200
    assert (
        restore_response.json()["plan"]["summary"]
        == "成都一日游版本1"
    )

    restored_history = client.get(
        f"/trips/{trip_id}/plans"
    ).json()

    assert [
        item["version"]
        for item in restored_history
    ] == [3, 2, 1]
    assert (
        restored_history[0]["plan"]["summary"]
        == "成都一日游版本1"
    )
    executions_response = client.get(
        f"/trips/{trip_id}/executions"
    )

    assert executions_response.status_code == 200

    executions = executions_response.json()

    assert len(executions) == 2
    assert [
        execution["mode"]
        for execution in executions
    ] == ["stream", "stream"]
    assert [
        execution["status"]
        for execution in executions
    ] == ["completed", "completed"]
    assert [
        execution["current_node"]
        for execution in executions
    ] == ["complete", "complete"]
    assert [
        execution["plan_version"]
        for execution in executions
    ] == [2, 1]
    assert all(
        execution["completed_at"] is not None
        for execution in executions
    )
