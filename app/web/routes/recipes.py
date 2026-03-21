"""Recipes routes: list all recipes, edit/delete individual recipe."""

import os
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.db.database import delete_recipe, get_all_recipes, get_review_count, update_recipe
from app.web.templating import templates as _templates

router = APIRouter()
_DB = Path(os.getenv("DB_PATH", "/app/db/archive.db"))


def _ctx(request: Request, **kwargs) -> dict:
    return {"request": request, "review_count": get_review_count(_DB), **kwargs}


@router.get("/recipes", response_class=HTMLResponse)
async def recipes_list(request: Request, q: str = ""):
    recipes = get_all_recipes(query=q, db_path=_DB)
    ctx = _ctx(request, recipes=recipes, q=q)
    if request.headers.get("hx-request"):
        return _templates.TemplateResponse("recipes_results.html", ctx)
    return _templates.TemplateResponse("recipes.html", ctx)


@router.post("/recipes/{recipe_id}")
async def recipe_update(
    recipe_id: int,
    article_id: int = Form(...),
    name: str = Form(""),
    category: str = Form(""),
    servings: str = Form(""),
    prep_time: str = Form(""),
    ingredients: str = Form(""),
    instructions: str = Form(""),
):
    update_recipe(recipe_id, {
        "name":         name or None,
        "category":     category or None,
        "servings":     servings or None,
        "prep_time":    prep_time or None,
        "ingredients":  ingredients or None,
        "instructions": instructions or None,
    }, _DB)
    return RedirectResponse(f"/articles/{article_id}/edit", status_code=303)


@router.post("/recipes/{recipe_id}/delete")
async def recipe_delete(recipe_id: int, article_id: int = Form(...)):
    delete_recipe(recipe_id, _DB)
    return RedirectResponse(f"/articles/{article_id}/edit", status_code=303)
