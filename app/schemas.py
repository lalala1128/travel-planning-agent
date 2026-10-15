import re
from typing import Any, List, Literal
from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserLogin(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("Invalid email address")
        return normalized


class UserRegister(UserLogin):
    password: str = Field(min_length=12, max_length=128)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    created_at: datetime


class SessionTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime


TripStatus = Literal[
    "draft",
    "planning",
    "completed",
    "failed",
]


class TripCreate(BaseModel):
    destination: str = Field(min_length=2, max_length=50)
    start_date: date
    days: int = Field(ge=1, le=15)
    budget: float = Field(gt=0)
    interests: List[str] = Field(default_factory=list)


class TripResponse(TripCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: TripStatus = "draft"


class TripStatusUpdate(BaseModel):
    status: TripStatus


class PlanActivity(BaseModel):
    time: str
    place: str
    activity: str
    transport: str
    distance_from_previous_km: float = Field(ge=0)
    estimated_travel_minutes: int = Field(ge=0)
    estimated_cost: float = Field(ge=0)

class DayPlan(BaseModel):
    day: int = Field(ge=1)
    date: date
    weather: str
    theme: str
    daily_estimated_cost: float = Field(ge=0)
    activities: List[PlanActivity]


class PlanSource(BaseModel):
    title: str
    source: str
    excerpt: str


class GeneratedPlan(BaseModel):
    destination: str
    summary: str
    total_estimated_cost: float = Field(ge=0)
    days: List[DayPlan]
    sources: List[PlanSource] = Field(default_factory=list)
class TripPlanHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    trip_id: int
    version: int = Field(ge=1)
    plan: GeneratedPlan
    created_at: datetime
class TripExecutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    trip_id: int
    mode: Literal["sync", "stream"]
    status: Literal["planning", "completed", "failed"]
    current_node: str | None = None
    error: str | None = None
    plan_version: int | None = None
    started_at: datetime
    completed_at: datetime | None = None

class Attraction(BaseModel):
    name: str
    category: str
    latitude: float
    longitude: float
    distance_from_center_km: float = Field(ge=0)


class RouteStop(BaseModel):
    day: int = Field(ge=1)
    order: int = Field(ge=1)
    name: str
    latitude: float
    longitude: float
    distance_from_previous_km: float = Field(ge=0)
    suggested_transport: str
    estimated_minutes: int = Field(ge=0)


class KnowledgeSearchResult(BaseModel):
    text: str
    source: str
    title: str
    city: str | None = None
    chunk_index: int = Field(ge=0)

    vector_score: float
    keyword_score: float = Field(ge=0.0, le=1.0)
    final_score: float

class SearchQueryAnalysis(BaseModel):
    original_query: str
    rewritten_query: str
    detected_city: str | None = None
    city_conflict: bool = False


class KnowledgeSearchResponse(BaseModel):
    query: str
    rewritten_query: str
    detected_city: str | None = None
    city_conflict: bool = False
    results: List[KnowledgeSearchResult]
class KnowledgeAskRequest(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    city: str | None = Field(default=None, max_length=50)
    limit: int = Field(default=3, ge=1, le=5)


class KnowledgeCitation(BaseModel):
    title: str
    source: str
    chunk_index: int = Field(ge=0)
    excerpt: str
    score: float


class KnowledgeAnswerResponse(BaseModel):
    query: str
    rewritten_query: str
    detected_city: str | None = None
    city_conflict: bool = False
    answer: str
    citations: List[KnowledgeCitation] = Field(
        default_factory=list
    )


class KnowledgeIngestResponse(BaseModel):
    source: str
    city: str | None = None
    chunks: int = Field(ge=1)


class TaskStateResponse(BaseModel):
    trip_id: int
    status: TripStatus
    node: str | None = None
    message: str | None = None
    error: str | None = None
    plan: dict[str, Any] | None = None


class TripDetailResponse(TripResponse):
    plan: GeneratedPlan | None = None


class TripPlanResponse(BaseModel):
    trip_id: int
    status: TripStatus
    plan: GeneratedPlan
