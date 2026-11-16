from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app import schemas
from app.api.deps import DB, Paging, get_or_404
from app.engine.checks import CHECKS, CheckConfigError, validate_rule
from app.models import CheckResult, DataSource, ValidationRule

router = APIRouter(prefix="/rules", tags=["validation rules"])


@router.get("/types", response_model=list[schemas.RuleTypeOut])
def list_rule_types():
    return [
        {"rule_type": name, "requires_column": cls.requires_column, "description": cls.description}
        for name, cls in sorted(CHECKS.items())
    ]


@router.post("", response_model=schemas.RuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(payload: schemas.RuleCreate, db: DB):
    get_or_404(db, DataSource, payload.data_source_id)
    rule = ValidationRule(**payload.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.get("", response_model=schemas.Page[schemas.RuleOut])
def list_rules(db: DB, page: Paging, data_source_id: int | None = Query(default=None)):
    stmt = select(ValidationRule)
    count = select(func.count()).select_from(ValidationRule)
    if data_source_id is not None:
        stmt = stmt.where(ValidationRule.data_source_id == data_source_id)
        count = count.where(ValidationRule.data_source_id == data_source_id)
    items = db.scalars(stmt.order_by(ValidationRule.id).limit(page.limit).offset(page.offset)).all()
    return {"items": items, "total": db.scalar(count), "limit": page.limit, "offset": page.offset}


@router.get("/{rule_id}", response_model=schemas.RuleOut)
def get_rule(rule_id: int, db: DB):
    return get_or_404(db, ValidationRule, rule_id)


@router.patch("/{rule_id}", response_model=schemas.RuleOut)
def update_rule(rule_id: int, payload: schemas.RuleUpdate, db: DB):
    rule = get_or_404(db, ValidationRule, rule_id)
    changes = payload.model_dump(exclude_unset=True)
    merged = {
        "table_name": changes.get("table_name", rule.table_name),
        "column_name": changes.get("column_name", rule.column_name),
        "config": changes.get("rule_config", rule.rule_config),
    }
    try:  # re-validate the rule as it will look after the update
        validate_rule(rule.rule_type, merged["table_name"], merged["column_name"], merged["config"])
    except CheckConfigError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    for key, value in changes.items():
        setattr(rule, key, value)
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: int, db: DB):
    db.delete(get_or_404(db, ValidationRule, rule_id))
    db.commit()


@router.get("/{rule_id}/results", response_model=schemas.Page[schemas.CheckResultOut])
def rule_history(rule_id: int, db: DB, page: Paging):
    """Result history for one rule, newest first: useful for trend charts."""
    get_or_404(db, ValidationRule, rule_id)
    where = CheckResult.rule_id == rule_id
    total = db.scalar(select(func.count()).select_from(CheckResult).where(where))
    items = db.scalars(
        select(CheckResult)
        .where(where)
        .order_by(CheckResult.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return {"items": items, "total": total, "limit": page.limit, "offset": page.offset}
