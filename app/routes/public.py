from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates


router = APIRouter(tags=["public-pages"])
templates = Jinja2Templates(
	directory=str(Path(__file__).resolve().parents[1] / "templates")
)


@router.get("/about")
async def about_page(request: Request):
	return templates.TemplateResponse("public/about.html", {"request": request})


@router.get("/contact")
async def contact_page(request: Request):
	return templates.TemplateResponse(
		"public/contact.html",
		{"request": request, "success": None, "error": None},
	)


@router.get("/faq")
async def faq_page(request: Request):
	return templates.TemplateResponse("public/faq.html", {"request": request})


@router.get("/pricing")
async def pricing_page(request: Request):
	return templates.TemplateResponse("public/pricing.html", {"request": request})


@router.get("/services")
async def services_page(request: Request):
	return templates.TemplateResponse("public/services.html", {"request": request})


@router.get("/setup-payment")
async def setup_payment_page(request: Request):
	return templates.TemplateResponse("public/setup-payment.html", {"request": request})
