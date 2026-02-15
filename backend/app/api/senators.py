from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import SenatorSchema, StateCountSchema
from app.services.senator_service import (
    get_senator_by_id,
    get_senators_by_state,
    get_states_with_counts,
)

router = APIRouter()


@router.get("/senators/states", response_model=list[StateCountSchema])
def list_states(db: Session = Depends(get_db)) -> list[StateCountSchema]:
    """Return all states that have senator data, with counts."""
    return get_states_with_counts(db)


@router.get("/senators/{senator_id}", response_model=SenatorSchema)
def get_senator(senator_id: str, db: Session = Depends(get_db)) -> SenatorSchema:
    """Return a single senator by ID."""
    result = get_senator_by_id(db, senator_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Senator not found")
    return result


@router.get("/senators", response_model=list[SenatorSchema])
def list_senators(
    state: str = Query(..., min_length=2, max_length=2, description="Two-letter state code"),
    db: Session = Depends(get_db),
) -> list[SenatorSchema]:
    """Return senators filtered by state."""
    return get_senators_by_state(db, state)
