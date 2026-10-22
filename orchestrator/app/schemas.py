from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.app.models import TaskStatus


class TaskCreate(BaseModel):
    milestone_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=250)
    description: str = Field(default="", max_length=10000)


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    milestone_id: int
    title: str
    description: str
    status: TaskStatus
    branch_name: str | None
    worktree_path: str | None
    created_at: datetime
