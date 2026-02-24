from sqlalchemy import text
from alembic import op
from datetime import datetime

def upgrade():
    # Create driver_notifications table
    op.execute("""
        CREATE TABLE IF NOT EXISTS driver_notifications (
            id SERIAL PRIMARY KEY,
            driver_id INTEGER NOT NULL,
            notif_type VARCHAR(20) NOT NULL,
            message TEXT NOT NULL,
            is_read BOOLEAN DEFAULT FALSE,
            delivered_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW()
        );
        
        CREATE INDEX IF NOT EXISTS idx_driver_notifications_driver_id ON driver_notifications(driver_id);
        CREATE INDEX IF NOT EXISTS idx_driver_notifications_delivered_at ON driver_notifications(delivered_at);
        CREATE INDEX IF NOT EXISTS idx_driver_notifications_created_at ON driver_notifications(created_at);
    """)

def downgrade():
    op.execute("DROP TABLE IF EXISTS driver_notifications CASCADE;")
