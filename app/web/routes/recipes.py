"""Recipes routes: edit/delete individual recipe."""

import os
from pathlib import Path

from fastapi import APIRouter, Form
from fastapi.responses import RedirectResponse

from app.db.database import delete_recipe, update_recipe

router = APIRouter()
_DB = Path(os.getenv("DB_PATH", "/app/db/archive.db"))


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
