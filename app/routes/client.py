@router.post("/onboarding/claim-handle")
async def claim_handle(handle: str = Form(...), db: Session = Depends(get_db)):
    # ... logic to save handle to display_name ...
    
    # REDIRECT TO THE BRIEFCASE (Step 3)
    return RedirectResponse(url="/onboarding/step3", status_code=302)