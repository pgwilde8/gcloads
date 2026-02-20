from sqlalchemy.orm import Session
from app.models.operations import Negotiation, Message
from app.models.broker import Broker
from app.models.load import Load

def process_scraped_load(db: Session, load_id: int):
    # 1. Fetch the load
    load = db.query(Load).filter(Load.id == load_id).first()
    
    # 2. Find the Broker email
    # Extract MC from 'ref_id' (e.g., 'TS-123456') or 'raw_data'
    mc_number = load.ref_id.split('-')[-1] 
    broker = db.query(Broker).filter(Broker.mc_number == mc_number).first()
    
    if broker and broker.primary_email:
        # 3. Create a Negotiation entry
        neg = Negotiation(
            load_id=load.id,
            driver_id=load.driver_id, # Link to the scout who found it
            status="DRAFT",
            current_offer=load.price
        )
        db.add(neg)
        db.flush() # Get the neg.id
        
        return {"status": "matched", "email": broker.primary_email}
    
    return {"status": "no_contact_found"}