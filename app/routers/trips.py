import json
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Response,
    status,
)
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from ..auth import get_current_tenant
from ..agents.travel_graph import (
    run_travel_graph,
    stream_travel_graph,
)

from ..database import get_db, get_session_factory
from ..models import Tenant, Trip, TripExecution, TripPlanHistory
from ..schemas import (
    TripCreate,
    TripDetailResponse,
    TripResponse,
    TripStatus,
    TripStatusUpdate,
    GeneratedPlan,
    TripPlanResponse,
    TaskStateResponse,
    TripPlanHistoryResponse,
    TripExecutionResponse,
)
from ..services.cache import get_task_state, set_task_state


router = APIRouter(
    prefix="/trips",
    tags=["Trips"],
)
STALE_EXECUTION_MINUTES = 30

def get_trip_or_404(db: Session, trip_id: int, tenant_id: int) -> Trip:
    trip = db.scalar(
        select(Trip).where(Trip.id == trip_id, Trip.tenant_id == tenant_id)
    )

    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trip not found",
        )

    return trip


def lock_trip_for_mutation(db: Session, trip_id: int, tenant_id: int) -> Trip:
    trip = db.scalar(
        select(Trip)
        .where(Trip.id == trip_id, Trip.tenant_id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    return trip


def reject_active_planning(trip: Trip) -> None:
    if trip.status == "planning":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trip planning already in progress",
        )


def acquire_trip_planning_lock(
    db: Session,
    trip_id: int,
    tenant_id: int,
) -> Trip:
    trip = get_trip_or_404(db, trip_id, tenant_id)
    now = datetime.now()

    if trip.status == "planning":
        active_execution = db.scalar(
            select(TripExecution)
            .where(
                TripExecution.trip_id == trip_id,
                TripExecution.status == "planning",
            )
            .order_by(TripExecution.id.desc())
            .limit(1)
        )

        stale_before = now - timedelta(
            minutes=STALE_EXECUTION_MINUTES
        )
        is_stale = (
            active_execution is not None
            and active_execution.started_at <= stale_before
        )

        if not is_stale:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Trip planning already in progress",
            )

        active_execution.status = "failed"
        active_execution.current_node = "failed"
        active_execution.error = (
            f"任务超过{STALE_EXECUTION_MINUTES}分钟"
            "未完成，已自动释放"
        )
        active_execution.completed_at = now
        trip.status = "failed"

        db.flush()

    result = db.execute(
        update(Trip)
        .where(
            Trip.id == trip_id,
            Trip.tenant_id == tenant_id,
            Trip.status != "planning",
        )
        .values(status="planning")
    )

    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trip planning already in progress",
        )

    db.refresh(trip)

    return trip
def save_plan_history(
    db: Session,
    trip: Trip,
    plan_data: dict,
) -> TripPlanHistory:
    locked_trip = lock_trip_for_mutation(db, trip.id, trip.tenant_id)
    latest_version = db.scalar(
        select(TripPlanHistory.version)
        .where(TripPlanHistory.trip_id == trip.id)
        .order_by(TripPlanHistory.version.desc())
        .limit(1)
        .with_for_update()
    )
    next_version = (latest_version or 0) + 1

    history = TripPlanHistory(
        trip_id=trip.id,
        version=next_version,
        plan=plan_data,
    )

    db.add(history)
    locked_trip.plan = plan_data

    return history


def create_trip_execution(
    db: Session,
    trip_id: int,
    mode: str,
) -> TripExecution:
    execution = TripExecution(
        trip_id=trip_id,
        mode=mode,
        status="planning",
        current_node="start",
    )
    db.add(execution)

    return execution

@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=TripResponse,
)
def create_trip(
    trip_data: TripCreate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    trip = Trip(**trip_data.model_dump(), tenant_id=tenant.id)

    db.add(trip)
    db.commit()
    db.refresh(trip)

    return trip


@router.get("", response_model=List[TripResponse])
def get_trips(
    destination: Optional[str] = None,
    trip_status: Optional[TripStatus] = Query(
        default=None,
        alias="status",
    ),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    statement = select(Trip).where(Trip.tenant_id == tenant.id)

    if destination is not None:
        statement = statement.where(
            Trip.destination.contains(destination)
        )

    if trip_status is not None:
        statement = statement.where(
            Trip.status == trip_status
        )

    statement = statement.offset(skip).limit(limit)

    return db.scalars(statement).all()


@router.get("/{trip_id}", response_model=TripDetailResponse)
def get_trip(
    trip_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    return get_trip_or_404(db, trip_id, tenant.id)
@router.get(
    "/{trip_id}/plans",
    response_model=list[TripPlanHistoryResponse],
)
def get_trip_plan_history(
    trip_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    get_trip_or_404(db, trip_id, tenant.id)

    statement = (
        select(TripPlanHistory)
        .where(TripPlanHistory.trip_id == trip_id)
        .order_by(TripPlanHistory.version.desc())
    )

    return db.scalars(statement).all()

@router.get(
    "/{trip_id}/executions",
    response_model=list[TripExecutionResponse],
)
def get_trip_executions(
    trip_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    get_trip_or_404(db, trip_id, tenant.id)

    statement = (
        select(TripExecution)
        .where(TripExecution.trip_id == trip_id)
        .order_by(TripExecution.id.desc())
    )

    return db.scalars(statement).all()
@router.post(
    "/{trip_id}/plans/{version}/restore",
    response_model=TripPlanResponse,
)
def restore_trip_plan(
    trip_id: int,
    version: int = Path(ge=1),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    trip = lock_trip_for_mutation(db, trip_id, tenant.id)
    reject_active_planning(trip)

    history = db.scalar(
        select(TripPlanHistory).where(
            TripPlanHistory.trip_id == trip_id,
            TripPlanHistory.version == version,
        )
    )

    if history is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trip plan version not found",
        )

    restored_plan = GeneratedPlan.model_validate(
        history.plan
    )
    plan_data = restored_plan.model_dump(mode="json")

    save_plan_history(db, trip, plan_data)
    trip.status = "completed"

    db.commit()
    db.refresh(trip)

    set_task_state(
        trip_id,
        "completed",
        node="restore",
        message=f"已恢复历史方案版本{version}",
        plan=trip.plan,
    )

    return {
        "trip_id": trip.id,
        "status": trip.status,
        "plan": trip.plan,
    }
@router.put("/{trip_id}", response_model=TripResponse)
def update_trip(
    trip_id: int,
    trip_data: TripCreate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    trip = lock_trip_for_mutation(db, trip_id, tenant.id)
    reject_active_planning(trip)

    for field, value in trip_data.model_dump().items():
        setattr(trip, field, value)

    db.commit()
    db.refresh(trip)

    return trip


@router.patch(
    "/{trip_id}/status",
    response_model=TripResponse,
)
def update_trip_status(
    trip_id: int,
    status_data: TripStatusUpdate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    trip = lock_trip_for_mutation(db, trip_id, tenant.id)
    reject_active_planning(trip)
    if status_data.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Planning status is managed by the server",
        )

    trip.status = status_data.status

    db.commit()
    db.refresh(trip)

    return trip


@router.delete(
    "/{trip_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_trip(
    trip_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    trip = lock_trip_for_mutation(db, trip_id, tenant.id)
    reject_active_planning(trip)

    db.delete(trip)
    db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{trip_id}/task",
    response_model=TaskStateResponse,
)
def get_trip_task(
    trip_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    trip = get_trip_or_404(db, trip_id, tenant.id)
    task = get_task_state(trip_id)

    if task is not None:
        return task

    return {
        "trip_id": trip.id,
        "status": trip.status,
        "plan": trip.plan,
    }

@router.post(
    "/{trip_id}/generate-plan",
    response_model=TripPlanResponse,
)
def generate_trip_plan(
    trip_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    trip = acquire_trip_planning_lock(
        db,
        trip_id,
        tenant.id,
    )
    execution = create_trip_execution(
        db,
        trip_id,
        mode="sync",
    )

    db.commit()
    db.refresh(execution)
    execution_id = execution.id
    set_task_state(
        trip_id,
        "planning",
        node="start",
        message="旅行计划任务已开始",
    )

    try:
        generated_plan = run_travel_graph(trip)

        plan_data = generated_plan.model_dump(mode="json")
        history = save_plan_history(
            db,
            trip,
            plan_data,
        )
        trip.status = "completed"

        execution.status = "completed"
        execution.current_node = "complete"
        execution.plan_version = history.version
        execution.completed_at = datetime.now()

        db.commit()
        db.refresh(trip)
        set_task_state(
            trip_id,
            "completed",
            node="complete",
            message="旅行计划生成成功",
            plan=trip.plan,
        )

    except Exception as exc:
        db.rollback()

        trip = db.get(Trip, trip_id)
        execution = db.get(
            TripExecution,
            execution_id,
        )

        if trip is not None:
            trip.status = "failed"

        if execution is not None:
            execution.status = "failed"
            execution.current_node = "failed"
            execution.error = str(exc)
            execution.completed_at = datetime.now()

        db.commit()

        set_task_state(
            trip_id,
            "failed",
            node="failed",
            message="旅行计划生成失败",
            error=str(exc),
        )

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"旅行计划生成失败：{exc}",
        )

    return {
        "trip_id": trip.id,
        "status": trip.status,
        "plan": trip.plan,
    }


def format_sse(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


@router.post("/{trip_id}/generate-plan/stream")
def generate_trip_plan_stream(
    trip_id: int,
    db: Session = Depends(get_db),
    session_factory=Depends(get_session_factory),
    tenant: Tenant = Depends(get_current_tenant),
):
    trip = acquire_trip_planning_lock(
        db,
        trip_id,
        tenant.id,
    )
    execution = create_trip_execution(
        db,
        trip_id,
        mode="stream",
    )

    db.commit()
    db.refresh(execution)
    execution_id = execution.id

    def event_stream():
        with session_factory() as stream_db:
            stream_trip = get_trip_or_404(stream_db, trip_id, tenant.id)
            execution = stream_db.get(
                TripExecution,
                execution_id,
            )

            if execution is None:
                raise RuntimeError(
                    "找不到流式任务执行记录"
                )


            set_task_state(
                trip_id,
                "planning",
                node="start",
                message="旅行计划任务已开始",
            )
            yield format_sse(
                "progress",
                {
                    "trip_id": trip_id,
                    "node": "start",
                    "message": "旅行计划任务已开始",
                },
            )

            try:
                for update in stream_travel_graph(stream_trip):
                    plan = update.pop("plan", None)

                    if plan is not None:
                        plan_data = plan.model_dump(mode="json")
                        history = save_plan_history(
                            stream_db,
                            stream_trip,
                            plan_data,
                        )
                        stream_trip.status = "completed"

                        execution.status = "completed"
                        execution.current_node = "complete"
                        execution.plan_version = history.version
                        execution.completed_at = datetime.now()

                        stream_db.commit()

                        set_task_state(
                            trip_id,
                            "completed",
                            node="complete",
                            message=update["message"],
                            plan=plan_data,
                        )
                        update["trip_id"] = trip_id
                        update["status"] = "completed"
                        update["plan"] = plan_data
                        yield format_sse("complete", update)
                        continue
                    execution.current_node = update["node"]
                    stream_db.commit()
                    set_task_state(
                        trip_id,
                        "planning",
                        node=update["node"],
                        message=update["message"],
                        error=update["error"],
                    )
                    update["trip_id"] = trip_id
                    update["status"] = "planning"
                    yield format_sse("progress", update)

            except Exception as exc:
                stream_db.rollback()
                stream_trip = stream_db.get(Trip, trip_id)
                execution = stream_db.get(
                    TripExecution,
                    execution_id,
                )

                if stream_trip is not None:
                    stream_trip.status = "failed"

                if execution is not None:
                    execution.status = "failed"
                    execution.current_node = "failed"
                    execution.error = str(exc)
                    execution.completed_at = datetime.now()

                stream_db.commit()

                set_task_state(
                    trip_id,
                    "failed",
                    node="failed",
                    message="旅行计划生成失败",
                    error=str(exc),
                )
                yield format_sse(
                    "error",
                    {
                        "trip_id": trip_id,
                        "status": "failed",
                        "error": str(exc),
                    },
                )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
