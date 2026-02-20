from sqlalchemy.orm import Session
from app.models.broker import Broker

def get_broker_contact(db: Session, mc_number: str):
    # This reaches into the webwise schema we just filled!
    return db.query(Broker).filter(Broker.mc_number == mc_number).first()