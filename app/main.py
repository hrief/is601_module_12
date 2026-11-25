from contextlib import asynccontextmanager
from datetime import datetime
from math import isfinite
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_active_user
from app.database import Base, engine, get_db
from app.models.calculation import Calculation
from app.models.user import User
from app.schemas.calculation import CalculationBase, CalculationCreate, CalculationRead, CalculationUpdate
from app.schemas.token import TokenResponse
from app.schemas.user import UserCreate, UserLogin, UserResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Calculations API",
    description="API for managing calculations",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError):
    errors = [
        {field: error[field] for field in ("loc", "msg", "type")}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.get("/health", tags=["health"])
def read_health():
    return {"status": "ok"}


@app.post("/auth/register", response_model=UserResponse, status_code=201, tags=["auth"])
@app.post("/users/register", response_model=UserResponse, status_code=201, tags=["users"])
def register(user_create: UserCreate, db: Session = Depends(get_db)):
    user_data = user_create.model_dump(exclude={"confirm_password"})
    try:
        user = User.register(db, user_data)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        if getattr(exc.orig, "pgcode", None) != "23505":
            raise
        raise HTTPException(status_code=400, detail="Username or email already exists") from exc
    db.refresh(user)
    return user


def authenticate(db: Session, username: str, password: str) -> dict:
    auth_result = User.authenticate(db, username, password)
    if auth_result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    db.commit()
    return auth_result


@app.post("/auth/login", response_model=TokenResponse, tags=["auth"])
@app.post("/users/login", response_model=TokenResponse, tags=["users"])
def login_json(user_login: UserLogin, db: Session = Depends(get_db)):
    auth_result = authenticate(db, user_login.username, user_login.password)
    user = auth_result["user"]
    return TokenResponse(
        access_token=auth_result["access_token"],
        refresh_token=auth_result["refresh_token"],
        token_type="bearer",
        expires_at=auth_result["expires_at"],
        user_id=user.id,
        username=user.username,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        is_active=user.is_active,
        is_verified=user.is_verified,
    )


@app.post("/auth/token", tags=["auth"])
def login_form(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Issue an access token for Swagger UI's Authorize dialog."""
    auth_result = authenticate(db, form_data.username, form_data.password)
    return {"access_token": auth_result["access_token"], "token_type": "bearer"}


def calculate_result(calculation: Calculation) -> float:
    result = calculation.get_result()
    if not isfinite(result):
        raise ValueError("Calculation result must be finite.")
    return result


def get_owned_calculation(calc_id: str, user_id: UUID, db: Session) -> Calculation:
    try:
        calc_uuid = UUID(calc_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid calculation id format.") from exc
    calculation = db.query(Calculation).filter(
        Calculation.id == calc_uuid,
        Calculation.user_id == user_id,
    ).first()
    if calculation is None:
        raise HTTPException(status_code=404, detail="Calculation not found.")
    return calculation


@app.post("/calculations", response_model=CalculationRead, status_code=201, tags=["calculations"])
def create_calculation(
    calculation_data: CalculationBase,
    current_user: UserResponse = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Compute a result and assign ownership from the access token."""
    owned_data = CalculationCreate(**calculation_data.model_dump(), user_id=current_user.id)
    calculation = Calculation.create(owned_data.type, owned_data.user_id, owned_data.inputs)
    try:
        calculation.result = calculate_result(calculation)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.add(calculation)
    db.commit()
    db.refresh(calculation)
    return calculation


@app.get("/calculations", response_model=list[CalculationRead], tags=["calculations"])
def list_calculations(
    current_user: UserResponse = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    return db.query(Calculation).filter(Calculation.user_id == current_user.id).all()


@app.get("/calculations/{calc_id}", response_model=CalculationRead, tags=["calculations"])
def get_calculation(
    calc_id: str,
    current_user: UserResponse = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    return get_owned_calculation(calc_id, current_user.id, db)


@app.put("/calculations/{calc_id}", response_model=CalculationRead, tags=["calculations"])
def update_calculation(
    calc_id: str,
    calculation_update: CalculationUpdate,
    current_user: UserResponse = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    calculation = get_owned_calculation(calc_id, current_user.id, db)
    if calculation_update.inputs is not None:
        calculation.inputs = calculation_update.inputs
        try:
            calculation.result = calculate_result(calculation)
        except ValueError as exc:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    calculation.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(calculation)
    return calculation


@app.delete("/calculations/{calc_id}", status_code=204, tags=["calculations"])
def delete_calculation(
    calc_id: str,
    current_user: UserResponse = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    calculation = get_owned_calculation(calc_id, current_user.id, db)
    db.delete(calculation)
    db.commit()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8001, log_level="info")
