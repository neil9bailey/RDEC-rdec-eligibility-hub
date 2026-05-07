from pydantic import BaseModel


class ScoreResult(BaseModel):
    score: int
    rating: str
    rating_label: str
    blockers: list[str]
    warnings: list[str]
    positive_indicators: list[str]
    recommended_next_actions: list[str]


class EntitlementResult(BaseModel):
    status: str
    rationale: str


class AIFSelectionResult(BaseModel):
    project_count: int
    total_qualifying_expenditure: float
    selected_project_ids: list[int]
    selected_qualifying_expenditure: float
    coverage_percentage: float
    minimum_described_projects: int
    ready: bool
    warnings: list[str]
