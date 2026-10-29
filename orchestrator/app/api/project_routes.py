# HTTP endpoints to create and list projects and their milestones.
# Returns 404 for a missing project and a clear error for a duplicate name.
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.app.api.schemas import (
    MilestoneCreate,
    MilestoneResponse,
    ProjectCreate,
    ProjectResponse,
)
from orchestrator.app.db.database import get_db
from orchestrator.app.db.models import Milestone, Project

router = APIRouter(prefix="/projects", tags=["projects"])


def _get_project_or_404(session: Session, project_id: int) -> Project:
    """Load the project, or answer 404 if it does not exist."""
    project = session.get(Project, project_id)

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project with ID {project_id} does not exist.",
        )

    return project


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_project_endpoint(
    payload: ProjectCreate,
    session: Session = Depends(get_db),
) -> Project:
    """Create a project."""
    project = Project(
        name=payload.name.strip(),
        repository_url=payload.repository_url,
        local_path=payload.local_path,
    )
    session.add(project)

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project '{payload.name.strip()}' already exists.",
        ) from exc

    session.refresh(project)
    return project


@router.get("", response_model=list[ProjectResponse])
def list_projects_endpoint(session: Session = Depends(get_db)) -> list[Project]:
    """List all projects."""
    return list(session.scalars(select(Project).order_by(Project.id)))


@router.post(
    "/{project_id}/milestones",
    response_model=MilestoneResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_milestone_endpoint(
    project_id: int,
    payload: MilestoneCreate,
    session: Session = Depends(get_db),
) -> Milestone:
    """Create a milestone inside a project."""
    project = _get_project_or_404(session, project_id)

    milestone = Milestone(
        project_id=project.id,
        code=payload.code.strip(),
        title=payload.title.strip(),
        description=payload.description,
    )
    session.add(milestone)
    session.commit()
    session.refresh(milestone)
    return milestone


@router.get(
    "/{project_id}/milestones",
    response_model=list[MilestoneResponse],
)
def list_milestones_endpoint(
    project_id: int,
    session: Session = Depends(get_db),
) -> list[Milestone]:
    """List the milestones of a project."""
    project = _get_project_or_404(session, project_id)

    return list(
        session.scalars(
            select(Milestone)
            .where(Milestone.project_id == project.id)
            .order_by(Milestone.id)
        )
    )
