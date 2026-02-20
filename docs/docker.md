Service Name (in Compose),Container Name,Purpose
db,gcloads_db,"The Vault: Your PostgreSQL database. It stores driver profiles, load details, and the ""3 Musketeers"" negotiation history."
app,gcloads_api,"The Front Office: Your FastAPI web server. This powers the dashboard Joe sees and the API endpoints (like the one that saves ""Floor Rates"")."
gcloads_inbound,gcloads_inbound,The Back Office: The inbound_listener.py script. It constantly polls the dispatch@ inbox and uses the new Extraction Logic to file emails.

cd /srv/gcloads-app && docker-compose restart app inbound
cd /srv/gcloads-app && docker-compose restart app
***
cd /srv/gcloads-app && docker-compose up -d --build
cd /srv/gcloads-app && docker-compose logs -f

cd /srv/gcloads-app && docker-compose ps

cd /srv/gcloads-app && docker-compose ps
(.venv) root@GreenCandleDispatch:/srv/gcloads-app# cd /srv/gcloads-app && docker-compose ps
     Name                    Command               State                    Ports                  
---------------------------------------------------------------------------------------------------
gcloads_api       uvicorn app.main:app --hos ...   Up      0.0.0.0:8369->8369/tcp,:::8369->8369/tcp
gcloads_db        docker-entrypoint.sh postgres    Up      5432/tcp                                
gcloads_inbound   python inbound_listener.py       Up  

rebuild: cd /srv/gcloads-app && docker-compose up -d --build