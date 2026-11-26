"""系统自检 API。"""

from fastapi import APIRouter, Query

from src.modules.administration.selfcheck import list_selfcheck_items, run_selfcheck

router = APIRouter()


@router.get("/selfcheck")
async def selfcheck(
    notify_send: bool = Query(False, description="是否真实发送通知测试(默认只校验配置不发送)"),
    list_only: bool = Query(False, alias="list", description="只列出待检项不探测(前端先渲染列表)"),
    keys: str | None = Query(None, description="逗号分隔,只探测这些 key(前端逐项更新进度)"),
):
    """一键体检 数据源 / AI / 通知。

    - `?list=1`:只返回待检项身份 `{items:[{category,key,name}]}`,不探测。
    - `?keys=ds:1,ai:2`:只探测这些项(逐项进度)。
    - 无参:探测全部。
    """
    if list_only:
        return {"items": list_selfcheck_items()}
    key_list = [k for k in keys.split(",") if k] if keys else None
    return await run_selfcheck(notify_send=notify_send, keys=key_list)


@router.get("/deep")
async def deep_health():
    """Deep liveness for the container HEALTHCHECK (unauthenticated).

    Shallow /api/health stays 200 even when the agent scheduler registered
    zero workflows (the 2026-11 AU-gating outage). This endpoint 503s when
    the DB is unreachable OR the agent scheduler has no jobs, so Docker
    marks the container unhealthy instead of silently serving.
    """
    from fastapi.responses import JSONResponse
    from sqlalchemy import text as _text

    from src.platform.persistence.database import SessionLocal

    db_ok = False
    try:
        db = SessionLocal()
        db.execute(_text("SELECT 1"))
        db.close()
        db_ok = True
    except Exception:
        pass

    agent_jobs = -1
    try:
        from src.platform.scheduling import scheduler_registry

        sched = scheduler_registry.get_all().get("agent")
        agent_jobs = len(sched.get_jobs()) if sched is not None else 0
    except Exception:
        agent_jobs = -1

    ok = db_ok and agent_jobs > 0
    return JSONResponse(
        {"ok": ok, "db": db_ok, "agent_jobs": agent_jobs},
        status_code=200 if ok else 503,
    )
